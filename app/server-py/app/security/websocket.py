"""WebSocket security implementation with SocketIO."""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
import socketio

from .guards import WebSocketGuard
from .environment import SecurityConfig

logger = logging.getLogger(__name__)


class SecureNamespace(socketio.AsyncNamespace):
    """Secure WebSocket namespace with authentication."""

    def __init__(self, namespace, config: SecurityConfig, cache=None, app_state=None):
        super().__init__(namespace)
        self.config = config
        self.cache = cache
        self.app_state = app_state
        self.guard = WebSocketGuard(config, cache)
        self.sessions = {}  # Store session data by socket ID

    async def on_connect(self, sid, environ, auth):
        """Handle WebSocket connection with authentication."""
        try:
            # Authenticate the connection
            claims = await self.guard.authenticate(auth)

            if not claims:
                await self.disconnect(sid)
                return False

            # Store session data
            session_data = {
                "claims": claims,
                "sid": sid,
                "course_id": claims.get("course_id"),
                "user_id": claims.get("user_id"),
                "user_name": claims.get("user_name", "Anonymous"),
                "is_instructor": claims.get("is_instructor", False),
                "is_student": claims.get("is_student", False),
                "connected_at": datetime.utcnow().isoformat()
            }

            self.sessions[sid] = session_data

            # Join course room
            course_id = claims.get("course_id")
            if course_id:
                await self.enter_room(sid, f"course:{course_id}")

                # Join role-specific room
                if claims.get("is_instructor"):
                    await self.enter_room(sid, f"instructors:{course_id}")
                else:
                    await self.enter_room(sid, f"students:{course_id}")

            # Track presence
            await self._track_presence(claims, "connect")

            logger.info(
                f"WebSocket connected: user={claims.get('user_id')} "
                f"course={course_id} role={'instructor' if claims.get('is_instructor') else 'student'}"
            )

            # Send connection success
            await self.emit("connected", {
                "user_id": claims.get("user_id"),
                "course_id": course_id,
                "is_instructor": claims.get("is_instructor")
            }, room=sid)

            return True

        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            await self.disconnect(sid)
            return False

    async def on_disconnect(self, sid):
        """Handle WebSocket disconnection."""
        session = self.sessions.get(sid)
        if session:
            await self._track_presence(session["claims"], "disconnect")

            logger.info(
                f"WebSocket disconnected: user={session['user_id']} "
                f"course={session['course_id']}"
            )

            # Clean up session
            del self.sessions[sid]

    async def on_confused(self, sid, data):
        """Handle student confusion event."""
        session = self.sessions.get(sid)
        if not session:
            return {"error": "Not authenticated"}

        # Verify student role
        if not await self.guard.verify_student_action(session, "confused"):
            return {"error": "Only students can report confusion"}

        # Check rate limiting
        if await self._check_rate_limit(session["user_id"], "confused"):
            return {"error": "Rate limit exceeded", "retry_after": 60}

        # Process confusion
        course_id = session["course_id"]
        user_id = session["user_id"]

        if self.app_state:
            # Update application state
            await self.app_state.process_confusion(
                course_id=course_id,
                user_id=user_id,
                user_name=session["user_name"],
                timestamp=datetime.utcnow()
            )

        # Broadcast metrics update
        await self._broadcast_metrics(course_id)

        logger.info(f"Confusion reported: user={user_id} course={course_id}")
        return {"status": "recorded"}

    async def on_presence(self, sid, data):
        """Handle presence heartbeat."""
        session = self.sessions.get(sid)
        if not session:
            return {"error": "Not authenticated"}

        # Update last seen
        session["last_seen"] = datetime.utcnow().isoformat()

        # Update presence tracking
        await self._track_presence(session["claims"], "heartbeat")

        return {"status": "ok"}

    async def on_get_metrics(self, sid, data):
        """Handle metrics request."""
        session = self.sessions.get(sid)
        if not session:
            return {"error": "Not authenticated"}

        course_id = session["course_id"]

        if self.app_state:
            metrics = await self.app_state.get_metrics(course_id)
            return {"metrics": metrics}

        return {"metrics": None}

    async def on_clear_confusion(self, sid, data):
        """Handle instructor clearing confusion."""
        session = self.sessions.get(sid)
        if not session:
            return {"error": "Not authenticated"}

        # Verify instructor role
        if not await self.guard.verify_instructor_action(session, "clear_confusion"):
            return {"error": "Only instructors can clear confusion"}

        course_id = session["course_id"]

        if self.app_state:
            await self.app_state.clear_confusion(course_id)

        # Broadcast update
        await self._broadcast_metrics(course_id)

        logger.info(f"Confusion cleared: course={course_id} by={session['user_id']}")
        return {"status": "cleared"}

    async def on_send_tutoring(self, sid, data):
        """Handle instructor sending tutoring content."""
        session = self.sessions.get(sid)
        if not session:
            return {"error": "Not authenticated"}

        # Verify instructor role
        if not await self.guard.verify_instructor_action(session, "send_tutoring"):
            return {"error": "Only instructors can send tutoring"}

        course_id = session["course_id"]
        content = data.get("content", "")

        # Broadcast tutoring content to confused students
        await self.emit(
            "tutoring",
            {
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
                "from": session["user_name"]
            },
            room=f"students:{course_id}"
        )

        logger.info(f"Tutoring sent: course={course_id} by={session['user_id']}")
        return {"status": "sent"}

    async def _track_presence(self, claims: Dict[str, Any], action: str):
        """Track user presence."""
        if not self.cache:
            return

        user_id = claims.get("user_id")
        course_id = claims.get("course_id")

        if not user_id or not course_id:
            return

        presence_key = f"presence:{course_id}:{user_id}"

        if action == "connect":
            # Mark as present
            self.cache.set(presence_key, "1", ex=300)  # 5 minute TTL
        elif action == "disconnect":
            # Remove presence
            self.cache.delete(presence_key)
        elif action == "heartbeat":
            # Refresh TTL
            self.cache.expire(presence_key, 300)

    async def _check_rate_limit(self, user_id: str, action: str) -> bool:
        """Check if user has exceeded rate limit."""
        if not self.cache:
            return False

        key = f"rate:{action}:{user_id}"
        count = self.cache.incr(key)

        if count == 1:
            # Set expiry on first increment
            self.cache.expire(key, 60)  # 1 minute window

        max_allowed = self.config.max_confusion_per_user if action == "confused" else 10
        return count > max_allowed

    async def _broadcast_metrics(self, course_id: str):
        """Broadcast updated metrics to course."""
        if not self.app_state:
            return

        metrics = await self.app_state.get_metrics(course_id)

        await self.emit(
            "metrics",
            {
                "metrics": metrics,
                "timestamp": datetime.utcnow().isoformat()
            },
            room=f"course:{course_id}"
        )

    def get_session(self, sid: str) -> Optional[Dict[str, Any]]:
        """Get session data for a socket ID."""
        return self.sessions.get(sid)


def create_secure_socketio(config: SecurityConfig, cache=None, app_state=None):
    """Create a secure SocketIO server."""
    sio = socketio.AsyncServer(
        cors_allowed_origins=config.allowed_origins,
        async_mode="asgi"
    )

    # Register secure namespace
    namespace = SecureNamespace("/", config, cache, app_state)
    sio.register_namespace(namespace)

    return sio