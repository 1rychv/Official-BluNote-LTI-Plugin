import time
import json
import secrets
from uuid import uuid4
from datetime import datetime
from typing import Dict, Any, Optional
from jose import jwt, JWTError
import httpx

from .config import lti_config
from .cache import token_cache
from .models import SessionClaims


class JWTSigner:
    def __init__(self, algorithm: str, key: str, kid: Optional[str] = None):
        self.algorithm = algorithm
        self.key = key
        self.kid = kid

    def sign(self, claims: Dict[str, Any]) -> str:
        headers = {"alg": self.algorithm}
        if self.kid:
            headers["kid"] = self.kid

        return jwt.encode(claims, self.key, algorithm=self.algorithm, headers=headers)

    def verify(self, token: str) -> Dict[str, Any]:
        return jwt.decode(token, self.key, algorithms=[self.algorithm])


class LTIAuthenticator:
    def __init__(self):
        self.lti_signer = JWTSigner(
            algorithm="RS256",
            key=lti_config.LTI_PRIVATE_KEY_PEM,
            kid=lti_config.LTI_KID
        )
        self.session_signer = JWTSigner(
            algorithm="HS256",
            key=lti_config.JWT_SIGNING_SECRET
        )

    async def issue_launch_token(self, claims: Dict[str, Any]) -> Dict[str, Any]:
        token_id = str(uuid4())
        now = int(time.time())

        session_claims = {
            "jti": token_id,
            "sub": claims["sub"],
            "iss": "BluNote",
            "aud": "BluNote-frontend",
            "exp": now + lti_config.SESSION_TIMEOUT,
            "iat": now,
            "nbf": now,

            "course_id": self._extract_course_id(claims),
            "course_title": self._extract_course_title(claims),
            "user_id": claims["sub"],
            "user_name": claims.get("name", "Anonymous"),
            "user_email": claims.get("email"),
            "roles": claims.get("https://purl.imsglobal.org/spec/lti/claim/roles", []),
            "is_instructor": self._is_instructor_role(claims.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])),
            "is_student": self._is_student_role(claims.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])),

            "platform_issuer": claims["iss"],
            "deployment_id": claims.get("https://purl.imsglobal.org/spec/lti/claim/deployment_id"),

            "nrps_url": self._extract_nrps_url(claims),
            "ags_url": self._extract_ags_url(claims),
        }

        token = self.session_signer.sign(session_claims)

        await token_cache.set(
            f"session:{token_id}",
            json.dumps(session_claims),
            ttl=lti_config.SESSION_TIMEOUT
        )

        await token_cache.set(
            f"user-course:{claims['sub']}:{session_claims['course_id']}",
            token_id,
            ttl=lti_config.SESSION_TIMEOUT
        )

        return {
            "token": token,
            "token_id": token_id,
            "expires_in": lti_config.SESSION_TIMEOUT,
            "claims": session_claims
        }

    async def verify_session_token(self, token: str) -> Dict[str, Any]:
        try:
            claims = self.session_signer.verify(token)
            token_id = claims["jti"]

            cached = await token_cache.get(f"session:{token_id}")
            if not cached:
                raise JWTError("Token expired or revoked")

            await token_cache.expire(f"session:{token_id}", lti_config.SESSION_TIMEOUT)
            return claims

        except JWTError as e:
            raise JWTError(f"Invalid token: {str(e)}")

    async def fetch_platform_jwks(self, jwks_url: str) -> Dict[str, Any]:
        cache_key = f"jwks:{jwks_url}"
        cached = await token_cache.get(cache_key)

        if cached:
            return json.loads(cached)

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
            jwks = response.json()

        await token_cache.set(cache_key, json.dumps(jwks), ttl=3600)
        return jwks

    async def fetch_platform_config(self, issuer: str) -> Dict[str, Any]:
        cache_key = f"platform_config:{issuer}"
        cached = await token_cache.get(cache_key)

        if cached:
            return json.loads(cached)

        well_known_url = f"{issuer}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(well_known_url)
            response.raise_for_status()
            config = response.json()

        await token_cache.set(cache_key, json.dumps(config), ttl=3600)
        return config

    def find_platform_key(self, keys: list, kid: Optional[str]) -> Optional[Dict[str, Any]]:
        if kid:
            for key in keys:
                if key.get("kid") == kid:
                    return key
        return keys[0] if keys else None

    def _extract_course_id(self, claims: Dict[str, Any]) -> str:
        context = claims.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
        if context.get("id"):
            return context["id"]

        resource_link = claims.get("https://purl.imsglobal.org/spec/lti/claim/resource_link", {})
        if resource_link.get("id"):
            return resource_link["id"]

        return "course-default"

    def _extract_course_title(self, claims: Dict[str, Any]) -> Optional[str]:
        context = claims.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
        return context.get("title")

    def _extract_nrps_url(self, claims: Dict[str, Any]) -> Optional[str]:
        nrps = claims.get("https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice", {})
        return nrps.get("context_memberships_url")

    def _extract_ags_url(self, claims: Dict[str, Any]) -> Optional[str]:
        ags = claims.get("https://purl.imsglobal.org/spec/lti-ags/claim/endpoint", {})
        return ags.get("lineitem")

    def _is_instructor_role(self, roles: list) -> bool:
        instructor_roles = [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
            "http://purl.imsglobal.org/vocab/lis/v2/membership#ContentDeveloper",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Faculty",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Administrator",
            "Instructor", "Teacher", "Faculty", "Administrator"
        ]
        return any(role in str(roles) for role in instructor_roles)

    def _is_student_role(self, roles: list) -> bool:
        student_roles = [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Student",
            "Learner", "Student"
        ]
        return any(role in str(roles) for role in student_roles)

    async def store_oidc_state(self, state: str, nonce: str, login_hint: str) -> None:
        state_data = {
            "nonce": nonce,
            "login_hint": login_hint,
            "created_at": datetime.utcnow().isoformat()
        }
        await token_cache.set(f"oidc_state:{state}", json.dumps(state_data), ttl=600)

    async def get_oidc_state(self, state: str) -> Optional[Dict[str, Any]]:
        cached = await token_cache.get(f"oidc_state:{state}")
        if cached:
            await token_cache.delete(f"oidc_state:{state}")
            return json.loads(cached)
        return None


lti_auth = LTIAuthenticator()