import time
from typing import Dict, Any, Optional
from jose import JWTError

from .auth import lti_auth


class WebSocketAuthenticator:
    """WebSocket authentication for Socket.IO"""

    async def authenticate_connection(self, auth: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Authenticate WebSocket connection"""
        if not auth or "token" not in auth:
            return None

        token = auth["token"]

        try:
            claims = await lti_auth.verify_session_token(token)
            return claims
        except JWTError:
            return None

    def create_session_data(self, claims: Dict[str, Any]) -> Dict[str, Any]:
        """Create session data for WebSocket"""
        return {
            "claims": claims,
            "course_id": claims["course_id"],
            "user_id": claims["user_id"],
            "is_instructor": claims.get("is_instructor", False),
            "is_student": claims.get("is_student", False),
            "connected_at": time.time()
        }

    def get_room_names(self, claims: Dict[str, Any]) -> list:
        """Get room names for user"""
        course_id = claims["course_id"]
        rooms = [f"course:{course_id}"]

        if claims.get("is_instructor", False):
            rooms.append(f"instructors:{course_id}")
        elif claims.get("is_student", False):
            rooms.append(f"students:{course_id}")

        return rooms


websocket_auth = WebSocketAuthenticator()