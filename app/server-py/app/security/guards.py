"""Authentication guards for REST and WebSocket endpoints."""

from typing import Optional, Dict, Any
from fastapi import Depends, HTTPException, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging

from .auth import verify_token as verify_session_token
from .environment import SecurityConfig

logger = logging.getLogger(__name__)

# Security scheme
security = HTTPBearer()


class AuthGuard:
    """Base authentication guard."""

    def __init__(self, config: SecurityConfig, cache=None):
        self.config = config
        self.cache = cache

    async def verify_token(
        self,
        credentials: HTTPAuthorizationCredentials = Security(security)
    ) -> Dict[str, Any]:
        """Verify JWT token from Authorization header."""
        if not credentials:
            raise HTTPException(status_code=401, detail="Missing credentials")

        token = credentials.credentials

        # Verify the token
        claims = verify_session_token(token, self.config, self.cache)

        if not claims:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        return claims


class RestGuard(AuthGuard):
    """REST API authentication guard."""

    async def __call__(
        self,
        credentials: HTTPAuthorizationCredentials = Security(security)
    ) -> Dict[str, Any]:
        """Verify token for REST endpoints."""
        return await self.verify_token(credentials)

    async def verify_course_access(
        self,
        course_id: str,
        claims: Dict[str, Any]
    ) -> bool:
        """Verify user has access to the specified course."""
        user_course_id = claims.get("course_id")

        if not user_course_id or user_course_id != course_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied to this course"
            )

        return True

    async def verify_instructor(self, claims: Dict[str, Any]) -> bool:
        """Verify user has instructor role."""
        if not claims.get("is_instructor"):
            raise HTTPException(
                status_code=403,
                detail="Instructor access required"
            )
        return True

    async def verify_student(self, claims: Dict[str, Any]) -> bool:
        """Verify user has student role."""
        if not claims.get("is_student"):
            raise HTTPException(
                status_code=403,
                detail="Student access required"
            )
        return True


class WebSocketGuard(AuthGuard):
    """WebSocket authentication guard."""

    async def authenticate(self, auth_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Authenticate WebSocket connection."""
        if not auth_data or "token" not in auth_data:
            logger.warning("WebSocket auth failed: missing token")
            return None

        token = auth_data["token"]

        # Verify the token
        claims = verify_session_token(token, self.config, self.cache)

        if not claims:
            logger.warning("WebSocket auth failed: invalid token")
            return None

        logger.info(f"WebSocket authenticated: user={claims.get('user_id')}")
        return claims

    async def verify_student_action(
        self,
        session: Dict[str, Any],
        action: str
    ) -> bool:
        """Verify student can perform the action."""
        if not session.get("is_student"):
            logger.warning(f"Non-student attempted {action}")
            return False
        return True

    async def verify_instructor_action(
        self,
        session: Dict[str, Any],
        action: str
    ) -> bool:
        """Verify instructor can perform the action."""
        if not session.get("is_instructor"):
            logger.warning(f"Non-instructor attempted {action}")
            return False
        return True


# Dependency injection functions for FastAPI
def create_rest_guard(config: SecurityConfig, cache=None):
    """Create REST guard for dependency injection."""
    guard = RestGuard(config, cache)
    return guard

def create_websocket_guard(config: SecurityConfig, cache=None):
    """Create WebSocket guard."""
    return WebSocketGuard(config, cache)


# Convenient dependency functions
async def rest_guard(
    credentials: HTTPAuthorizationCredentials = Security(security),
    request: Request = None
) -> Dict[str, Any]:
    """REST API guard dependency."""
    # Get config from app state
    if request and hasattr(request.app.state, "security_config"):
        config = request.app.state.security_config
        cache = getattr(request.app.state, "cache", None)
    else:
        # Fallback to loading config
        from .environment import load_security_config
        config = load_security_config()
        cache = None

    guard = RestGuard(config, cache)
    return await guard(credentials)


async def instructor_guard(
    claims: Dict[str, Any] = Depends(rest_guard)
) -> Dict[str, Any]:
    """Verify instructor role."""
    if not claims.get("is_instructor"):
        raise HTTPException(
            status_code=403,
            detail="Instructor access required"
        )
    return claims


async def student_guard(
    claims: Dict[str, Any] = Depends(rest_guard)
) -> Dict[str, Any]:
    """Verify student role."""
    if not claims.get("is_student"):
        raise HTTPException(
            status_code=403,
            detail="Student access required"
        )
    return claims


def websocket_guard(config: SecurityConfig, cache=None):
    """Create WebSocket guard for SocketIO."""
    return WebSocketGuard(config, cache)