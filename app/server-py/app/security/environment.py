"""Environment and configuration management for security."""

import os
from typing import Optional, List
from dataclasses import dataclass
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

@dataclass
class SecurityConfig:
    """Security configuration settings."""

    # Required fields (no defaults)
    # JWT Configuration
    lti_private_key_pem: str
    lti_kid: str
    jwt_signing_secret: str

    # Platform Configuration
    platform_issuer: str
    platform_client_id: str
    platform_jwks_url: str
    platform_auth_login_url: str

    # Security Settings
    allowed_origins: List[str]

    # Application Settings
    frontend_base_url: str
    tool_base_url: str

    # Optional fields (with defaults)
    jwt_algorithm: str = "HS256"
    lti_algorithm: str = "RS256"
    session_timeout: int = 300  # 5 minutes
    max_requests_per_min: int = 60
    max_confusion_per_user: int = 3
    enforce_https: bool = True

    # Redis Configuration
    redis_url: Optional[str] = None

    # PostgreSQL Configuration
    postgres_url: Optional[str] = None

def load_security_config() -> SecurityConfig:
    """Load security configuration from environment or vault."""
    load_dotenv()

    # In production, these would come from a secure vault
    # For now, we'll use environment variables

    # Parse allowed origins
    allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173")
    allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",")]

    # Load LTI private key (in production, from vault)
    lti_private_key_pem = os.getenv("LTI_PRIVATE_KEY_PEM", "")
    if not lti_private_key_pem and os.path.exists(".keys/private.key"):
        with open(".keys/private.key", "r") as f:
            lti_private_key_pem = f.read()

    config = SecurityConfig(
        # JWT Configuration
        lti_private_key_pem=lti_private_key_pem,
        lti_kid=os.getenv("LTI_KID", "bluenote-key-1"),
        jwt_signing_secret=os.getenv("JWT_SIGNING_SECRET", "dev-secret-change-in-production"),

        # Platform Configuration
        platform_issuer=os.getenv("PLATFORM_ISSUER", "https://moodle.example.edu"),
        platform_client_id=os.getenv("PLATFORM_CLIENT_ID", "bluenote-lti"),
        platform_jwks_url=os.getenv("PLATFORM_JWKS_URL", "https://moodle.example.edu/.well-known/jwks.json"),
        platform_auth_login_url=os.getenv("PLATFORM_AUTH_LOGIN_URL", "https://moodle.example.edu/mod/lti/auth.php"),

        # Security Settings
        allowed_origins=allowed_origins,
        session_timeout=int(os.getenv("SESSION_TIMEOUT", "300")),
        max_requests_per_min=int(os.getenv("MAX_REQUESTS_PER_MIN", "60")),
        max_confusion_per_user=int(os.getenv("MAX_CONFUSION_PER_USER", "3")),
        enforce_https=os.getenv("ENFORCE_HTTPS", "true").lower() == "true",

        # Database Configuration
        redis_url=os.getenv("REDIS_URL"),
        postgres_url=os.getenv("POSTGRES_URL"),

        # Application Settings
        frontend_base_url=os.getenv("FRONTEND_BASE_URL", "http://localhost:5173"),
        tool_base_url=os.getenv("TOOL_BASE_URL", "http://localhost:4000")
    )

    logger.info(f"Security config loaded - Platform: {config.platform_issuer}")
    return config