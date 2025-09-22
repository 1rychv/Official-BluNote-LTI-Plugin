"""JWT authentication and token management."""

import time
import json
import uuid
import jwt
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
import logging

from .environment import SecurityConfig

logger = logging.getLogger(__name__)

class JWTManager:
    """Manages JWT signing and verification."""

    def __init__(self, config: SecurityConfig):
        self.config = config
        self.session_secret = config.jwt_signing_secret
        self.lti_private_key = config.lti_private_key_pem
        self.lti_kid = config.lti_kid

    def sign_session_token(self, claims: Dict[str, Any]) -> str:
        """Sign a session token with HS256."""
        return jwt.encode(
            claims,
            self.session_secret,
            algorithm=self.config.jwt_algorithm
        )

    def verify_session_token(self, token: str) -> Dict[str, Any]:
        """Verify a session token."""
        return jwt.decode(
            token,
            self.session_secret,
            algorithms=[self.config.jwt_algorithm]
        )

    def sign_lti_token(self, claims: Dict[str, Any]) -> str:
        """Sign an LTI token with RS256."""
        if not self.lti_private_key:
            raise ValueError("LTI private key not configured")

        claims["kid"] = self.lti_kid
        return jwt.encode(
            claims,
            self.lti_private_key,
            algorithm=self.config.lti_algorithm,
            headers={"kid": self.lti_kid}
        )

    def verify_lti_token(self, token: str, public_key: str) -> Dict[str, Any]:
        """Verify an LTI token with platform's public key."""
        return jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True
            },
            audience=self.config.platform_client_id,
            issuer=self.config.platform_issuer
        )


class SessionToken:
    """Manages session token creation and validation."""

    def __init__(self, jwt_manager: JWTManager, cache=None):
        self.jwt_manager = jwt_manager
        self.cache = cache  # Redis cache in production
        self.config = jwt_manager.config

    def issue_launch_token(self, lti_claims: Dict[str, Any]) -> Dict[str, Any]:
        """Issue a session token from LTI launch claims."""
        token_id = str(uuid.uuid4())
        now = int(time.time())

        # Extract context and user info
        context = lti_claims.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
        roles = lti_claims.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])

        # Build session claims
        session_claims = {
            "jti": token_id,
            "sub": lti_claims["sub"],
            "iss": "bluenote",
            "aud": "bluenote-frontend",
            "exp": now + self.config.session_timeout,
            "iat": now,
            "nbf": now,

            # BluNote specific claims
            "course_id": context.get("id", ""),
            "course_title": context.get("title", ""),
            "user_id": lti_claims["sub"],
            "user_name": lti_claims.get("name", "Anonymous"),
            "user_email": lti_claims.get("email"),
            "roles": roles,
            "is_instructor": self._is_instructor_role(roles),
            "is_student": self._is_student_role(roles),

            # Platform tracking
            "platform_issuer": lti_claims["iss"],
            "deployment_id": lti_claims.get("https://purl.imsglobal.org/spec/lti/claim/deployment_id"),

            # Service URLs if available
            "nrps_url": self._extract_service_url(lti_claims, "nrps"),
            "ags_url": self._extract_service_url(lti_claims, "ags")
        }

        # Sign the token
        token = self.jwt_manager.sign_session_token(session_claims)

        # Cache the token if cache is available
        if self.cache:
            self._cache_token(token_id, session_claims)

        return {
            "token": token,
            "token_id": token_id,
            "expires_in": self.config.session_timeout,
            "claims": session_claims
        }

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and return token claims."""
        try:
            claims = self.jwt_manager.verify_session_token(token)

            # Check if token is cached (not revoked)
            if self.cache:
                token_id = claims.get("jti")
                cached = self.cache.get(f"session:{token_id}")
                if not cached:
                    return None

                # Refresh TTL on activity
                self.cache.expire(f"session:{token_id}", self.config.session_timeout)

            return claims

        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None

    def _is_instructor_role(self, roles: list) -> bool:
        """Check if roles include instructor permissions."""
        instructor_roles = [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
            "http://purl.imsglobal.org/vocab/lis/v2/membership#ContentDeveloper",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Faculty",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Administrator",
            "Instructor", "Teacher", "Faculty", "Administrator"
        ]
        return any(role in str(roles) for role in instructor_roles)

    def _is_student_role(self, roles: list) -> bool:
        """Check if roles include student permissions."""
        student_roles = [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Student",
            "Learner", "Student"
        ]
        return any(role in str(roles) for role in student_roles)

    def _extract_service_url(self, claims: Dict, service_type: str) -> Optional[str]:
        """Extract service URL from LTI claims."""
        if service_type == "nrps":
            nrps = claims.get("https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice", {})
            return nrps.get("context_memberships_url")
        elif service_type == "ags":
            ags = claims.get("https://purl.imsglobal.org/spec/lti-ags/claim/endpoint", {})
            return ags.get("lineitem")
        return None

    def _cache_token(self, token_id: str, claims: Dict[str, Any]):
        """Cache token with claims."""
        if not self.cache:
            return

        # Cache the token
        self.cache.set(
            f"session:{token_id}",
            json.dumps(claims),
            ex=self.config.session_timeout
        )

        # Also cache by user-course for presence tracking
        user_id = claims.get("user_id")
        course_id = claims.get("course_id")
        if user_id and course_id:
            self.cache.set(
                f"user-course:{user_id}:{course_id}",
                token_id,
                ex=self.config.session_timeout
            )


# Convenience functions
def issue_launch_token(lti_claims: Dict[str, Any], config: SecurityConfig, cache=None) -> Dict[str, Any]:
    """Issue a launch token from LTI claims."""
    jwt_manager = JWTManager(config)
    session_token = SessionToken(jwt_manager, cache)
    return session_token.issue_launch_token(lti_claims)

def verify_token(token: str, config: SecurityConfig, cache=None) -> Optional[Dict[str, Any]]:
    """Verify a session token."""
    jwt_manager = JWTManager(config)
    session_token = SessionToken(jwt_manager, cache)
    return session_token.verify_token(token)

def verify_lti_token(token: str, public_key: str, config: SecurityConfig) -> Dict[str, Any]:
    """Verify an LTI token."""
    jwt_manager = JWTManager(config)
    return jwt_manager.verify_lti_token(token, public_key)