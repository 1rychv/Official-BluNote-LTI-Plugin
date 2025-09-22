"""Security module for BluNote LTI application."""

from .auth import JWTManager, SessionToken, issue_launch_token, verify_token, verify_lti_token
from .environment import SecurityConfig, load_security_config
from .middleware import setup_security_middleware, RateLimiter
from .guards import (
    rest_guard,
    instructor_guard,
    student_guard,
    websocket_guard,
    create_rest_guard,
    create_websocket_guard
)
from .websocket import create_secure_socketio, SecureNamespace

__all__ = [
    'JWTManager',
    'SessionToken',
    'issue_launch_token',
    'verify_token',
    'verify_lti_token',
    'SecurityConfig',
    'load_security_config',
    'setup_security_middleware',
    'RateLimiter',
    'rest_guard',
    'instructor_guard',
    'student_guard',
    'websocket_guard',
    'create_rest_guard',
    'create_websocket_guard',
    'create_secure_socketio',
    'SecureNamespace'
]