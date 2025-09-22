import time
from uuid import uuid4
from typing import Dict, Any
from fastapi import Request, Response, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends, Security
from jose import JWTError

from .config import lti_config
from .auth import lti_auth


class SecurityMiddleware:
    """Security middleware for BluNote LTI application"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope, receive)

            # Add security headers
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = dict(message.get("headers", []))

                    # Content Security Policy for iframe embedding
                    csp = "default-src 'self'; "
                    csp += f"frame-ancestors {' '.join(lti_config.ALLOWED_ORIGINS)}; "
                    csp += "script-src 'self' 'unsafe-inline'; "
                    csp += "style-src 'self' 'unsafe-inline'; "
                    csp += "img-src 'self' data: https:; "
                    csp += "connect-src 'self' wss: https:;"

                    security_headers = {
                        b"content-security-policy": csp.encode(),
                        b"x-content-type-options": b"nosniff",
                        b"x-frame-options": b"SAMEORIGIN",
                        b"x-xss-protection": b"1; mode=block",
                        b"strict-transport-security": b"max-age=31536000; includeSubDomains",
                        b"referrer-policy": b"strict-origin-when-cross-origin",
                        b"x-request-id": str(uuid4()).encode(),
                    }

                    headers.update(security_headers)
                    message["headers"] = list(headers.items())

                await send(message)

            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)


class HTTPSRedirectMiddleware:
    """Redirect HTTP to HTTPS in production"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            scheme = headers.get(b"x-forwarded-proto", b"http").decode()

            if scheme != "https" and lti_config.PLATFORM_ISSUER:  # Only enforce in production
                # Redirect to HTTPS
                host = headers.get(b"host", b"localhost").decode()
                path = scope.get("path", "/")
                query_string = scope.get("query_string", b"").decode()

                https_url = f"https://{host}{path}"
                if query_string:
                    https_url += f"?{query_string}"

                response = Response(
                    status_code=301,
                    headers={"location": https_url}
                )

                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


security = HTTPBearer()


async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> Dict[str, Any]:
    """Verify session token for API endpoints"""
    token = credentials.credentials

    try:
        claims = await lti_auth.verify_session_token(token)
        return claims
    except JWTError as e:
        raise HTTPException(401, f"Invalid token: {str(e)}")


def require_instructor(claims: Dict[str, Any] = Depends(verify_token)) -> Dict[str, Any]:
    """Require instructor role"""
    if not claims.get("is_instructor", False):
        raise HTTPException(403, "Instructor role required")
    return claims


def require_student(claims: Dict[str, Any] = Depends(verify_token)) -> Dict[str, Any]:
    """Require student role"""
    if not claims.get("is_student", False):
        raise HTTPException(403, "Student role required")
    return claims


def require_course_access(course_id: str):
    """Require access to specific course"""
    def _verify_course_access(claims: Dict[str, Any] = Depends(verify_token)) -> Dict[str, Any]:
        if claims.get("course_id") != course_id:
            raise HTTPException(403, "Access denied to this course")
        return claims
    return _verify_course_access