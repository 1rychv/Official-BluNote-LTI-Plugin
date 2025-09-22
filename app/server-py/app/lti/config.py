import os
import secrets
import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from jose.utils import base64url_encode


class LTIConfig:
    def __init__(self):
        self.PLATFORM_ISSUER = os.getenv("PLATFORM_ISSUER")
        self.PLATFORM_CLIENT_ID = os.getenv("PLATFORM_CLIENT_ID")
        self.PLATFORM_AUTH_LOGIN_URL = os.getenv("PLATFORM_AUTH_LOGIN_URL")
        self.PLATFORM_JWKS_URL = os.getenv("PLATFORM_JWKS_URL")
        self.PLATFORM_DEPLOYMENT_ID = os.getenv("PLATFORM_DEPLOYMENT_ID")

        PORT = int(os.getenv("PORT", "4000"))
        self.TOOL_BASE = os.getenv("TOOL_BASE", f"http://localhost:{PORT}")
        self.FRONTEND_BASE = os.getenv("FRONTEND_BASE", "http://localhost:5173")

        self.LTI_PRIVATE_KEY_PEM = os.getenv("LTI_PRIVATE_KEY_PEM")
        self.LTI_KID = os.getenv("LTI_KID")

        self.SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT", "300"))
        self.MAX_REQUESTS_PER_MIN = int(os.getenv("MAX_REQUESTS_PER_MIN", "60"))
        self.MAX_CONFUSION_PER_USER = int(os.getenv("MAX_CONFUSION_PER_USER", "3"))

        allowed_origins = os.getenv("ALLOWED_ORIGINS", self.FRONTEND_BASE)
        self.ALLOWED_ORIGINS = [origin.strip() for origin in allowed_origins.split(",")]

        self.JWT_SIGNING_SECRET = os.getenv("JWT_SIGNING_SECRET", secrets.token_urlsafe(32))

        self._initialize_keys()

    def _initialize_keys(self):
        if not self.LTI_PRIVATE_KEY_PEM:
            self.LTI_PRIVATE_KEY_PEM, self.DEV_JWK = self._generate_rsa_key()
            self.LTI_KID = self.LTI_KID or self.DEV_JWK["kid"]
        else:
            self.DEV_JWK = self._derive_public_jwk()

    def _generate_rsa_key(self):
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )

        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode('utf-8')

        pub = key.public_key()
        pub_numbers = pub.public_numbers()

        e = pub_numbers.e.to_bytes((pub_numbers.e.bit_length() + 7) // 8, 'big')
        n = pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, 'big')

        jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": "dev-kid",
            "n": base64url_encode(n).decode('utf-8'),
            "e": base64url_encode(e).decode('utf-8'),
        }

        return pem, jwk

    def _derive_public_jwk(self):
        key = serialization.load_pem_private_key(
            self.LTI_PRIVATE_KEY_PEM.encode('utf-8'),
            password=None,
            backend=default_backend()
        )

        pub = key.public_key()
        pub_numbers = pub.public_numbers()

        e = pub_numbers.e.to_bytes((pub_numbers.e.bit_length() + 7) // 8, 'big')
        n = pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, 'big')

        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.LTI_KID or "tool-kid",
            "n": base64url_encode(n).decode('utf-8'),
            "e": base64url_encode(e).decode('utf-8'),
        }

    def get_tool_config(self, base_url: str) -> Dict[str, Any]:
        return {
            "title": "BluNote - Student Confusion Tracker",
            "description": "Real-time confusion tracking with AI-powered tutoring support",
            "oidc_initiation_url": f"{base_url}/lti/oidc_login",
            "target_link_uri": f"{base_url}/lti/launch",
            "scopes": [
                "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly",
                "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
                "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem.readonly",
                "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
                "https://purl.imsglobal.org/spec/lti-ags/scope/score"
            ],
            "extensions": [
                {
                    "platform": "canvas.instructure.com",
                    "settings": {
                        "privacy_level": "public",
                        "course_navigation": {
                            "enabled": True,
                            "text": "BluNote",
                            "default": "enabled"
                        }
                    }
                }
            ],
            "public_jwk_url": f"{base_url}/lti/.well-known/jwks.json",
            "custom_fields": {
                "confusion_threshold": "$Custom.confusion_threshold",
                "cooldown_minutes": "$Custom.cooldown_minutes"
            },
            "messages": [
                {
                    "type": "LtiResourceLinkRequest",
                    "target_link_uri": f"{base_url}/lti/launch",
                    "label": "BluNote Confusion Tracker",
                    "custom_parameters": {
                        "activity": "confusion_tracker"
                    }
                },
                {
                    "type": "LtiDeepLinkingRequest",
                    "target_link_uri": f"{base_url}/lti/launch",
                    "label": "Add BluNote Activity"
                }
            ]
        }

    def get_jwks(self) -> Dict[str, Any]:
        return {
            "keys": [
                {
                    **self.DEV_JWK,
                    "kid": self.LTI_KID or self.DEV_JWK.get('kid', 'dev-kid')
                }
            ]
        }


lti_config = LTIConfig()