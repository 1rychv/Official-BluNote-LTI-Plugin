"""Security middleware for FastAPI application."""

import time
import uuid
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from fastapi import Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

from .environment import SecurityConfig

logger = logging.getLogger(__name__)


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Middleware to redirect HTTP to HTTPS in production."""

    def __init__(self, app, enforce_https: bool = True):
        super().__init__(app)
        self.enforce_https = enforce_https

    async def dispatch(self, request: Request, call_next):
        if self.enforce_https and request.url.scheme == "http":
            # Don't redirect in local development
            if "localhost" not in str(request.url) and "127.0.0.1" not in str(request.url):
                url = request.url.replace(scheme="https")
                return RedirectResponse(url=str(url), status_code=301)

        response = await call_next(request)
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all requests and responses."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        start_time = time.time()

        # Add request ID to context
        request.state.request_id = request_id

        # Log request
        logger.info({
            "request_id": request_id,
            "method": request.method,
            "url": str(request.url),
            "client": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent")
        })

        try:
            response = await call_next(request)

            # Log response
            duration = time.time() - start_time
            logger.info({
                "request_id": request_id,
                "status": response.status_code,
                "duration_ms": round(duration * 1000, 2)
            })

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            duration = time.time() - start_time
            logger.error({
                "request_id": request_id,
                "error": str(e),
                "duration_ms": round(duration * 1000, 2)
            })
            raise


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to responses."""

    def __init__(self, app, config: SecurityConfig):
        super().__init__(app)
        self.config = config

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Build CSP for iframe embedding
        allowed_origins = " ".join(self.config.allowed_origins)
        csp = (
            "default-src 'self'; "
            f"frame-ancestors {allowed_origins}; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self' wss: https:; "
            "font-src 'self' data:;"
        )

        # Add security headers
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # HSTS header for HTTPS enforcement
        if self.config.enforce_https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response


class RateLimiter:
    """Rate limiting configuration."""

    def __init__(self, config: SecurityConfig, redis_url: Optional[str] = None):
        self.config = config
        self.storage_uri = redis_url or "memory://"

        # Create main limiter
        self.limiter = Limiter(
            key_func=get_remote_address,
            default_limits=[f"{config.max_requests_per_min}/minute"],
            storage_uri=self.storage_uri
        )

        # Create specific limiters
        self.confusion_limiter = self.limiter.limit(
            f"{config.max_confusion_per_user}/minute"
        )

    def get_limiter(self):
        return self.limiter

    def get_confusion_limiter(self):
        return self.confusion_limiter


def setup_security_middleware(app, config: SecurityConfig):
    """Configure all security middleware for the application."""

    # HTTPS Redirect
    if config.enforce_https:
        app.add_middleware(
            HTTPSRedirectMiddleware,
            enforce_https=config.enforce_https
        )

    # Request Logging
    app.add_middleware(RequestLoggingMiddleware)

    # Security Headers
    app.add_middleware(
        SecurityHeadersMiddleware,
        config=config
    )

    # CORS Configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=3600
    )

    # Rate Limiting
    rate_limiter = RateLimiter(config, config.redis_url)
    app.state.limiter = rate_limiter.get_limiter()
    app.add_exception_handler(429, _rate_limit_exceeded_handler)

    logger.info("Security middleware configured")
    return rate_limiter