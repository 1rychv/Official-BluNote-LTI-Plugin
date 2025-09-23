import secrets
import time
import json
from uuid import uuid4
from urllib.parse import urlencode
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from jose import jwt, JWTError

from .config import lti_config
from .auth import lti_auth
from .cache import token_cache
from .models import LTIDeepLinkingSelection


router = APIRouter()


@router.get("/.well-known/jwks.json")
async def get_jwks():
    """Return public keys for JWT verification"""
    return JSONResponse(content=lti_config.get_jwks())


@router.get("/config")
async def get_tool_config(request: Request):
    """Return tool configuration for platform registration"""
    base_url = str(request.base_url).rstrip('/')
    return JSONResponse(content=lti_config.get_tool_config(base_url))


@router.get("/oidc_login")
@router.post("/oidc_login")
async def oidc_login(request: Request):
    """Handle OIDC login initiation from platform"""

    # Extract parameters from query or form
    if request.method == "GET":
        params = request.query_params
    else:
        form_data = await request.form()
        params = form_data

    iss = params.get("iss")
    login_hint = params.get("login_hint")
    target_link_uri = params.get("target_link_uri")
    lti_message_hint = params.get("lti_message_hint")
    client_id = params.get("client_id")
    deployment_id = params.get("lti_deployment_id")

    # Validate platform
    if lti_config.PLATFORM_ISSUER and iss != lti_config.PLATFORM_ISSUER:
        raise HTTPException(400, f"Unknown platform issuer: {iss}")

    # Generate state and nonce
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    # Store state for validation
    await lti_auth.store_oidc_state(state, nonce, login_hint or "")

    # Build authorization redirect URL
    auth_params = {
        "response_type": "id_token",
        "response_mode": "form_post",
        "scope": "openid",
        "client_id": client_id or lti_config.PLATFORM_CLIENT_ID,
        "redirect_uri": target_link_uri or f"{lti_config.TOOL_BASE}/lti/launch",
        "login_hint": login_hint or "",
        "state": state,
        "nonce": nonce,
        "prompt": "none"
    }

    # Add optional parameters
    if lti_message_hint:
        auth_params["lti_message_hint"] = lti_message_hint
    if deployment_id:
        auth_params["lti_deployment_id"] = deployment_id

    # Get platform's authorization endpoint
    if lti_config.PLATFORM_AUTH_LOGIN_URL:
        auth_url = lti_config.PLATFORM_AUTH_LOGIN_URL
    elif lti_config.PLATFORM_ISSUER:
        try:
            platform_config = await lti_auth.fetch_platform_config(lti_config.PLATFORM_ISSUER)
            auth_url = platform_config["authorization_endpoint"]
        except Exception as e:
            raise HTTPException(500, f"Failed to fetch platform config: {str(e)}")
    else:
        raise HTTPException(500, "Platform not configured. Set PLATFORM_* environment variables.")

    # Redirect to platform for authentication
    redirect_url = f"{auth_url}?{urlencode(auth_params)}"

    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/launch")
async def lti_launch(id_token: str = Form(...), state: str = Form(None)):
    """Handle LTI 1.3 ResourceLinkRequest or DeepLinkingRequest launch"""

    if not id_token:
        raise HTTPException(400, "Missing id_token")

    # Validate state if provided
    if state:
        state_info = await lti_auth.get_oidc_state(state)
        if not state_info:
            raise HTTPException(401, "Invalid or expired state")

    # Verify JWT
    try:
        if lti_config.PLATFORM_JWKS_URL and lti_config.PLATFORM_ISSUER and lti_config.PLATFORM_CLIENT_ID:
            # Production: verify with platform's public keys
            platform_jwks = await lti_auth.fetch_platform_jwks(lti_config.PLATFORM_JWKS_URL)

            # Decode header to find key ID
            header = jwt.get_unverified_header(id_token)
            key = lti_auth.find_platform_key(platform_jwks.get("keys", []), header.get("kid"))

            if not key:
                raise HTTPException(401, "No matching key found")

            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=lti_config.PLATFORM_CLIENT_ID,
                issuer=lti_config.PLATFORM_ISSUER
            )
            verified = True
        else:
            # Development: accept unverified tokens
            claims = jwt.get_unverified_claims(id_token)
            verified = False

    except JWTError as e:
        raise HTTPException(401, f"Invalid token: {str(e)}")

    # Validate nonce if state was provided
    if state and state_info:
        if claims.get("nonce") != state_info["nonce"]:
            raise HTTPException(401, "Invalid nonce")

    # Validate LTI claims
    lti_version = claims.get("https://purl.imsglobal.org/spec/lti/claim/version")
    if lti_version != "1.3.0":
        raise HTTPException(400, f"Unsupported LTI version: {lti_version}")

    message_type = claims.get("https://purl.imsglobal.org/spec/lti/claim/message_type")
    if message_type not in ["LtiResourceLinkRequest", "LtiDeepLinkingRequest"]:
        raise HTTPException(400, f"Unsupported message type: {message_type}")

    # Extract user and course information
    user_info = _extract_user_info(claims)
    course_info = _extract_course_info(claims)

    # Store launch data (in production, this would go to a database)
    launch_id = str(uuid4())
    launch_data = {
        "launch_id": launch_id,
        "platform_issuer": claims["iss"],
        "deployment_id": claims.get("https://purl.imsglobal.org/spec/lti/claim/deployment_id"),
        "user_info": user_info,
        "course_info": course_info,
        "raw_claims": claims,
        "verified": verified,
        "timestamp": time.time()
    }

    # Cache launch data for 10 minutes
    await token_cache.set(f"launch:{launch_id}", json.dumps(launch_data, default=str), ttl=600)

    # Store course settings with service URLs in database
    await _store_course_settings(claims, course_info["id"])

    # Handle different message types
    if message_type == "LtiResourceLinkRequest":
        return await _handle_resource_link_request(claims, launch_data)
    elif message_type == "LtiDeepLinkingRequest":
        return await _handle_deep_linking_request(claims, launch_id)


@router.get("/deep_linking/{launch_id}")
async def deep_linking_selection(launch_id: str):
    """Show resource selection UI for deep linking"""

    # Retrieve launch data
    launch_data_str = await token_cache.get(f"launch:{launch_id}")
    if not launch_data_str:
        raise HTTPException(404, "Launch not found or expired")

    launch_data = json.loads(launch_data_str)

    # Generate selection page HTML
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>BluNote - Select Activity</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background: #f5f5f7;
                color: #1d1d1f;
            }}
            .container {{
                max-width: 600px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                text-align: center;
            }}
            .header h1 {{
                margin: 0;
                font-size: 28px;
                font-weight: 600;
            }}
            .content {{
                padding: 30px;
            }}
            .activity {{
                border: 2px solid #e5e5e7;
                border-radius: 8px;
                padding: 20px;
                margin: 15px 0;
                cursor: pointer;
                transition: all 0.3s ease;
                background: white;
            }}
            .activity:hover {{
                border-color: #667eea;
                background: #f8f9ff;
                transform: translateY(-2px);
                box-shadow: 0 8px 25px rgba(102, 126, 234, 0.15);
            }}
            .activity h3 {{
                margin: 0 0 10px 0;
                color: #1d1d1f;
                font-size: 20px;
                font-weight: 600;
            }}
            .activity p {{
                margin: 0;
                color: #6e6e73;
                line-height: 1.5;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Select BluNote Activity</h1>
            </div>
            <div class="content">
                <div class="activity" onclick="selectActivity('confusion_tracker')">
                    <h3>🤔 Confusion Tracker</h3>
                    <p>Real-time confusion monitoring with AI tutoring support. Students can report confusion, and when thresholds are met, personalized tutoring content is delivered.</p>
                </div>
                <div class="activity" onclick="selectActivity('analytics_dashboard')">
                    <h3>📊 Analytics Dashboard</h3>
                    <p>Historical confusion patterns and engagement metrics. Track student understanding over time with detailed analytics and insights.</p>
                </div>
            </div>
        </div>

        <script>
            function selectActivity(type) {{
                fetch('/lti/deep_linking/{launch_id}/select', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ activity_type: type }})
                }}).then(response => response.text())
                  .then(html => document.write(html))
                  .catch(error => {{
                    alert('Error selecting activity: ' + error);
                  }});
            }}
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@router.post("/deep_linking/{launch_id}/select")
async def deep_linking_select(launch_id: str, selection: LTIDeepLinkingSelection):
    """Handle resource selection and return to platform"""

    # Retrieve launch data
    launch_data_str = await token_cache.get(f"launch:{launch_id}")
    if not launch_data_str:
        raise HTTPException(404, "Launch not found or expired")

    launch_data = json.loads(launch_data_str)
    claims = launch_data["raw_claims"]

    # Get deep linking settings
    dl_settings = claims.get("https://purl.imsglobal.org/spec/lti-dl/claim/deep_linking_settings", {})
    return_url = dl_settings.get("deep_link_return_url")

    if not return_url:
        raise HTTPException(400, "No return URL in deep linking settings")

    # Build content item based on selection
    activity_type = selection.activity_type

    activity_configs = {
        "confusion_tracker": {
            "title": "BluNote Confusion Tracker",
            "text": "Real-time student confusion tracking with AI tutoring"
        },
        "analytics_dashboard": {
            "title": "BluNote Analytics Dashboard",
            "text": "Historical confusion patterns and engagement metrics"
        }
    }

    config = activity_configs.get(activity_type, activity_configs["confusion_tracker"])

    content_item = {
        "type": "ltiResourceLink",
        "title": config["title"],
        "text": config["text"],
        "url": f"{lti_config.TOOL_BASE}/lti/launch",
        "lineItem": {
            "scoreMaximum": 100,
            "label": "BluNote Participation",
            "resourceId": f"BluNote_{activity_type}",
            "tag": "participation"
        },
        "custom": {
            "activity_type": activity_type,
            "confusion_threshold": 25,
            "cooldown_minutes": 3
        }
    }

    # Create deep linking response JWT
    now = int(time.time())
    dl_response_claims = {
        "iss": lti_config.PLATFORM_CLIENT_ID,
        "aud": claims["iss"],
        "exp": now + 600,
        "iat": now,
        "nbf": now,
        "nonce": claims.get("nonce"),
        "azp": claims["iss"],
        "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiDeepLinkingResponse",
        "https://purl.imsglobal.org/spec/lti/claim/version": "1.3.0",
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id": claims.get("https://purl.imsglobal.org/spec/lti/claim/deployment_id"),
        "https://purl.imsglobal.org/spec/lti-dl/claim/content_items": [content_item],
        "https://purl.imsglobal.org/spec/lti-dl/claim/data": dl_settings.get("data")
    }

    # Sign with our private key
    dl_jwt = lti_auth.lti_signer.sign(dl_response_claims)

    # Return auto-submit form to platform
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Returning to Platform...</title>
        <style>
            body {{
                font-family: system-ui, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
                background: #f5f5f7;
            }}
            .loader {{
                text-align: center;
            }}
            .spinner {{
                border: 4px solid #e5e5e7;
                border-top: 4px solid #667eea;
                border-radius: 50%;
                width: 40px;
                height: 40px;
                animation: spin 1s linear infinite;
                margin: 0 auto 20px;
            }}
            @keyframes spin {{
                0% {{ transform: rotate(0deg); }}
                100% {{ transform: rotate(360deg); }}
            }}
        </style>
    </head>
    <body onload="document.forms[0].submit()">
        <div class="loader">
            <div class="spinner"></div>
            <p>Returning to your learning management system...</p>
        </div>
        <form method="POST" action="{return_url}" style="display: none;">
            <input type="hidden" name="JWT" value="{dl_jwt}">
        </form>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@router.get("/dev/launch")
async def dev_launch(
    role: str = 'student',
    courseId: str = 'COURSE1',
    userId: str = 'dev1',
    name: str = 'Dev User'
):
    """Development launch endpoint for testing"""
    if role.lower().startswith('inst'):
        url = f"{lti_config.FRONTEND_BASE}/instructor?courseId={courseId}&name={name}"
    else:
        url = f"{lti_config.FRONTEND_BASE}/student?courseId={courseId}&userId={userId}&name={name}"

    return JSONResponse({"redirect": url})


def _extract_user_info(claims: Dict[str, Any]) -> Dict[str, Any]:
    """Extract user information from LTI claims"""
    return {
        "id": claims["sub"],
        "name": claims.get("name", ""),
        "given_name": claims.get("given_name", ""),
        "family_name": claims.get("family_name", ""),
        "email": claims.get("email", ""),
        "roles": claims.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])
    }


def _extract_course_info(claims: Dict[str, Any]) -> Dict[str, Any]:
    """Extract course information from LTI claims"""
    context = claims.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
    return {
        "id": context.get("id", "course-default"),
        "title": context.get("title", ""),
        "label": context.get("label", "")
    }


async def _handle_resource_link_request(claims: Dict[str, Any], launch_data: Dict[str, Any]) -> RedirectResponse:
    """Handle standard LTI resource link launch"""

    # Create session token
    session_data = await lti_auth.issue_launch_token(claims)

    # Determine view based on role
    user_roles = claims.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])
    is_instructor = lti_auth._is_instructor_role(user_roles)

    view = "dashboard" if is_instructor else "student"
    course_id = _extract_course_info(claims)["id"]

    # Build frontend URL with token
    frontend_url = f"{lti_config.FRONTEND_BASE}/?token={session_data['token']}&view={view}&course={course_id}"

    return RedirectResponse(url=frontend_url, status_code=302)


async def _handle_deep_linking_request(claims: Dict[str, Any], launch_id: str) -> RedirectResponse:
    """Handle deep linking request"""

    # Redirect to resource selection page
    selection_url = f"{lti_config.TOOL_BASE}/lti/deep_linking/{launch_id}"
    return RedirectResponse(url=selection_url, status_code=302)


async def _store_course_settings(claims: Dict[str, Any], course_id: str) -> None:
    """Store course settings including NRPS and AGS URLs"""
    from ..database.connection import get_db_connection
    from datetime import datetime

    # Extract service URLs
    nrps = claims.get("https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice", {})
    ags = claims.get("https://purl.imsglobal.org/spec/lti-ags/claim/endpoint", {})

    settings = {
        "platform_issuer": claims["iss"],
        "deployment_id": claims.get("https://purl.imsglobal.org/spec/lti/claim/deployment_id"),
        "nrps_url": nrps.get("context_memberships_url"),
        "ags_url": ags.get("lineitems"),
        "ags_lineitem_url": ags.get("lineitem"),
        "client_id": claims.get("aud") if isinstance(claims.get("aud"), str) else claims.get("aud", [None])[0]
    }

    # Store in database
    conn = await get_db_connection()
    try:
        await conn.execute("""
            INSERT INTO courses (id, created_at, updated_at, settings)
            VALUES ($1, $2, $2, $3)
            ON CONFLICT (id) DO UPDATE SET
                updated_at = EXCLUDED.updated_at,
                settings = courses.settings || EXCLUDED.settings
        """, course_id, datetime.utcnow(), json.dumps(settings))
    finally:
        await conn.close()