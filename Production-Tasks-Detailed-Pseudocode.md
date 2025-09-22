# Production Tasks Detailed Pseudocode

Based on the BluNote LTI architecture and SPEC-1 requirements, this document provides expanded pseudocode for each production task.

## ~~1. Harden Authentication & Transport - DETAILED~~ ✅ COMPLETED

```pseudo
function secure_services():
    # ===== ENVIRONMENT & KEY MANAGEMENT =====
    load_production_env():
        # Load from secure vault (AWS Secrets Manager/HashiCorp Vault)
        LTI_PRIVATE_KEY_PEM = vault.get("bluenote/lti/private_key")
        LTI_KID = vault.get("bluenote/lti/kid")
        JWT_SIGNING_SECRET = vault.get("bluenote/jwt/secret")
        REDIS_URL = vault.get("bluenote/redis/url")
        POSTGRES_URL = vault.get("bluenote/postgres/url")

        # Platform specific configs
        PLATFORM_ISSUER = env.get("PLATFORM_ISSUER")  # e.g., "https://moodle.edu"
        PLATFORM_CLIENT_ID = env.get("PLATFORM_CLIENT_ID")
        PLATFORM_JWKS_URL = env.get("PLATFORM_JWKS_URL")

        # Security configs
        ALLOWED_ORIGINS = env.get("ALLOWED_ORIGINS").split(",")  # ["https://moodle.edu"]
        SESSION_TIMEOUT = 300  # 5 minutes
        MAX_REQUESTS_PER_MIN = 60
        MAX_CONFUSION_PER_USER = 3  # per minute

    # ===== JWT TOKEN INFRASTRUCTURE =====
    init_jwt_system():
        # Create JWT signer with RS256 for LTI
        lti_signer = jwt.Signer(
            algorithm="RS256",
            private_key=LTI_PRIVATE_KEY_PEM,
            kid=LTI_KID
        )

        # Create internal JWT signer with HS256 for session tokens
        session_signer = jwt.Signer(
            algorithm="HS256",
            secret=JWT_SIGNING_SECRET
        )

        # Initialize token cache (Redis with TTL)
        token_cache = RedisCache(
            prefix="tokens:",
            default_ttl=SESSION_TIMEOUT
        )

        return lti_signer, session_signer, token_cache

    # ===== LAUNCH TOKEN ISSUANCE =====
    define issue_launch_token(claims, session_signer, token_cache):
        # Generate unique token ID
        token_id = uuid4()

        # Build session claims from LTI launch
        session_claims = {
            "jti": token_id,
            "sub": claims["sub"],  # User ID from LTI
            "iss": "bluenote",
            "aud": "bluenote-frontend",
            "exp": time.now() + SESSION_TIMEOUT,
            "iat": time.now(),
            "nbf": time.now(),

            # BluNote specific claims
            "course_id": claims["https://purl.imsglobal.org/spec/lti/claim/context"]["id"],
            "course_title": claims["https://purl.imsglobal.org/spec/lti/claim/context"]["title"],
            "user_id": claims["sub"],
            "user_name": claims.get("name", "Anonymous"),
            "user_email": claims.get("email"),
            "roles": claims["https://purl.imsglobal.org/spec/lti/claim/roles"],
            "is_instructor": is_instructor_role(claims["roles"]),
            "is_student": is_student_role(claims["roles"]),

            # Platform tracking
            "platform_issuer": claims["iss"],
            "deployment_id": claims["https://purl.imsglobal.org/spec/lti/claim/deployment_id"],

            # Service URLs if available
            "nrps_url": claims.get("https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice", {}).get("context_memberships_url"),
            "ags_url": claims.get("https://purl.imsglobal.org/spec/lti-ags/claim/endpoint", {}).get("lineitem"),
        }

        # Sign the token
        token = session_signer.sign(session_claims)

        # Cache the token with claims for quick lookup
        token_cache.set(
            key=f"session:{token_id}",
            value=json.dumps(session_claims),
            ttl=SESSION_TIMEOUT
        )

        # Also cache by user-course for presence tracking
        token_cache.set(
            key=f"user-course:{claims['sub']}:{session_claims['course_id']}",
            value=token_id,
            ttl=SESSION_TIMEOUT
        )

        return {
            "token": token,
            "token_id": token_id,
            "expires_in": SESSION_TIMEOUT
        }

    # ===== LTI LAUNCH HANDLER UPDATE =====
    update_lti_launch_handler():
        @app.post("/lti/launch")
        async def lti_launch(request: Request):
            try:
                # Extract and verify the ID token from Moodle
                id_token = request.form.get("id_token")
                state = request.form.get("state")

                # Verify state matches what we sent during OIDC
                if not verify_state_nonce(state):
                    raise HTTPException(401, "Invalid state")

                # Fetch platform's public keys
                platform_keys = await fetch_platform_jwks(PLATFORM_JWKS_URL)

                # Verify the JWT signature and claims
                claims = jwt.decode(
                    id_token,
                    keys=platform_keys,
                    audience=PLATFORM_CLIENT_ID,
                    issuer=PLATFORM_ISSUER
                )

                # Validate required LTI claims
                validate_lti_claims(claims)

                # Issue our session token
                session_data = issue_launch_token(claims, session_signer, token_cache)

                # Log the launch for auditing
                await log_launch_event(
                    user_id=claims["sub"],
                    course_id=session_data["course_id"],
                    platform=PLATFORM_ISSUER,
                    timestamp=datetime.utcnow()
                )

                # Redirect to frontend with token
                frontend_url = f"{FRONTEND_BASE}/?token={session_data['token']}"

                # Add role-specific routing
                if session_data["is_instructor"]:
                    frontend_url += "&view=dashboard"
                else:
                    frontend_url += "&view=student"

                return RedirectResponse(url=frontend_url, status_code=302)

            except jwt.InvalidTokenError as e:
                logger.error(f"JWT validation failed: {e}")
                raise HTTPException(401, "Invalid token")
            except Exception as e:
                logger.error(f"Launch failed: {e}")
                raise HTTPException(500, "Launch failed")

    # ===== FASTAPI MIDDLEWARE CONFIGURATION =====
    configure_fastapi_middlewares(app):
        # HTTPS Redirect Middleware
        @app.middleware("http")
        async def https_redirect(request: Request, call_next):
            if not request.url.scheme == "https":
                if env.get("ENFORCE_HTTPS", "true") == "true":
                    url = request.url.replace(scheme="https")
                    return RedirectResponse(url=str(url), status_code=301)
            response = await call_next(request)
            return response

        # Rate Limiting Middleware
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.util import get_remote_address

        limiter = Limiter(
            key_func=get_remote_address,
            default_limits=[f"{MAX_REQUESTS_PER_MIN}/minute"],
            storage_uri=REDIS_URL
        )
        app.state.limiter = limiter
        app.add_exception_handler(429, _rate_limit_exceeded_handler)

        # Per-endpoint rate limits
        confusion_limiter = limiter.limit(f"{MAX_CONFUSION_PER_USER}/minute")

        # Request Logging Middleware
        @app.middleware("http")
        async def log_requests(request: Request, call_next):
            request_id = str(uuid4())
            start_time = time.time()

            # Add request ID to context
            request.state.request_id = request_id

            # Log request
            logger.info({
                "request_id": request_id,
                "method": request.method,
                "url": str(request.url),
                "client": request.client.host,
                "user_agent": request.headers.get("user-agent")
            })

            response = await call_next(request)

            # Log response
            duration = time.time() - start_time
            logger.info({
                "request_id": request_id,
                "status": response.status_code,
                "duration_ms": duration * 1000
            })

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        # Security Headers Middleware
        @app.middleware("http")
        async def add_security_headers(request: Request, call_next):
            response = await call_next(request)

            # Content Security Policy for iframe embedding
            csp = "default-src 'self'; "
            csp += f"frame-ancestors {' '.join(ALLOWED_ORIGINS)}; "
            csp += "script-src 'self' 'unsafe-inline'; "  # Adjust as needed
            csp += "style-src 'self' 'unsafe-inline'; "
            csp += "img-src 'self' data: https:; "
            csp += "connect-src 'self' wss: https:;"

            response.headers["Content-Security-Policy"] = csp
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

            return response

    # ===== REST API AUTHENTICATION GUARD =====
    apply_rest_guard():
        from fastapi import Depends, HTTPException, Security
        from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

        security = HTTPBearer()

        async def verify_token(
            credentials: HTTPAuthorizationCredentials = Security(security)
        ):
            token = credentials.credentials

            try:
                # Decode the JWT
                claims = session_signer.verify(token)

                # Check if token is in cache (not revoked)
                token_id = claims["jti"]
                cached = await token_cache.get(f"session:{token_id}")

                if not cached:
                    raise HTTPException(401, "Token expired or revoked")

                # Refresh TTL on activity
                await token_cache.expire(f"session:{token_id}", SESSION_TIMEOUT)

                return claims

            except jwt.ExpiredSignatureError:
                raise HTTPException(401, "Token expired")
            except jwt.InvalidTokenError:
                raise HTTPException(401, "Invalid token")

        # Apply to all API routes
        @app.get("/api/metrics/{course_id}")
        async def get_metrics(
            course_id: str,
            claims: dict = Depends(verify_token)
        ):
            # Verify user has access to this course
            if claims["course_id"] != course_id:
                raise HTTPException(403, "Access denied to this course")

            # Proceed with endpoint logic
            metrics = await calculate_metrics(course_id)
            return metrics

        @app.post("/api/confused")
        @confusion_limiter
        async def report_confused(
            request: ConfusedRequest,
            claims: dict = Depends(verify_token)
        ):
            # Ensure student can only report for themselves
            if not claims["is_student"]:
                raise HTTPException(403, "Only students can report confusion")

            if request.user_id != claims["user_id"]:
                raise HTTPException(403, "Cannot report for another user")

            # Process confusion event
            await process_confusion(
                course_id=claims["course_id"],
                user_id=claims["user_id"],
                timestamp=datetime.utcnow()
            )

            return {"status": "recorded"}

    # ===== WEBSOCKET AUTHENTICATION GUARD =====
    apply_socket_guard():
        from socketio import AsyncServer, AsyncNamespace

        sio = AsyncServer(
            cors_allowed_origins=ALLOWED_ORIGINS,
            async_mode="asgi"
        )

        class SecureNamespace(AsyncNamespace):
            async def on_connect(self, sid, environ, auth):
                try:
                    # Extract token from auth payload
                    if not auth or "token" not in auth:
                        await self.disconnect(sid)
                        return False

                    token = auth["token"]

                    # Verify the token
                    claims = session_signer.verify(token)

                    # Check cache
                    token_id = claims["jti"]
                    cached = await token_cache.get(f"session:{token_id}")

                    if not cached:
                        await self.disconnect(sid)
                        return False

                    # Store claims in session
                    await self.save_session(sid, {
                        "claims": claims,
                        "course_id": claims["course_id"],
                        "user_id": claims["user_id"],
                        "is_instructor": claims["is_instructor"],
                        "connected_at": datetime.utcnow().isoformat()
                    })

                    # Join course room
                    await self.enter_room(sid, f"course:{claims['course_id']}")

                    # Join role-specific room
                    if claims["is_instructor"]:
                        await self.enter_room(sid, f"instructors:{claims['course_id']}")
                    else:
                        await self.enter_room(sid, f"students:{claims['course_id']}")

                    # Track presence
                    await track_user_presence(
                        course_id=claims["course_id"],
                        user_id=claims["user_id"],
                        action="connect"
                    )

                    logger.info(f"WebSocket connected: {claims['user_id']} in course {claims['course_id']}")
                    return True

                except Exception as e:
                    logger.error(f"WebSocket auth failed: {e}")
                    await self.disconnect(sid)
                    return False

            async def on_disconnect(self, sid):
                session = await self.get_session(sid)
                if session:
                    await track_user_presence(
                        course_id=session["course_id"],
                        user_id=session["user_id"],
                        action="disconnect"
                    )
                    logger.info(f"WebSocket disconnected: {session['user_id']}")

            async def on_confused(self, sid, data):
                session = await self.get_session(sid)
                if not session or not session["claims"]["is_student"]:
                    return {"error": "Unauthorized"}

                # Apply rate limiting check
                if await check_rate_limit(session["user_id"], "confused"):
                    return {"error": "Rate limit exceeded"}

                # Process confusion
                await process_confusion(
                    course_id=session["course_id"],
                    user_id=session["user_id"],
                    timestamp=datetime.utcnow()
                )

                return {"status": "recorded"}

        # Register namespace
        sio.register_namespace(SecureNamespace("/"))

    # ===== CORS CONFIGURATION =====
    set_cors_policy(app):
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=ALLOWED_ORIGINS,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            max_age=3600
        )

    # ===== FRONTEND TOKEN HANDLING =====
    update_frontend_to_send_token():
        """
        Frontend implementation for token handling:

        // Extract token from URL on launch
        const urlParams = new URLSearchParams(window.location.search);
        const token = urlParams.get('token');

        if (token) {
            // Store securely
            sessionStorage.setItem('bluenote_token', token);

            // Clean URL
            window.history.replaceState({}, document.title, window.location.pathname);
        }

        // Configure HTTP client
        const api = axios.create({
            baseURL: API_BASE,
            headers: {
                'Authorization': `Bearer ${sessionStorage.getItem('bluenote_token')}`
            }
        });

        // Configure WebSocket with auth
        const socket = io(API_BASE, {
            auth: {
                token: sessionStorage.getItem('bluenote_token')
            },
            transports: ['websocket'],
            reconnection: true,
            reconnectionAttempts: 5
        });

        // Handle token expiry
        api.interceptors.response.use(
            response => response,
            error => {
                if (error.response?.status === 401) {
                    // Token expired - show re-launch message
                    alert('Session expired. Please relaunch from your LMS.');
                    sessionStorage.removeItem('bluenote_token');
                }
                return Promise.reject(error);
            }
        );
        """

    # ===== EXECUTION =====
    # Initialize all components
    load_production_env()
    lti_signer, session_signer, token_cache = init_jwt_system()

    # Apply all middleware and guards
    configure_fastapi_middlewares(app)
    apply_rest_guard()
    apply_socket_guard()
    set_cors_policy(app)

    # Update frontend instructions
    update_frontend_to_send_token()

    logger.info("Security hardening complete")
```

## ~~2. Complete LTI Flows (OIDC + Deep Linking) - DETAILED~~ ✅ COMPLETED

```pseudo
function complete_lti_flows():
    # ===== TOOL REGISTRATION CONFIGURATION =====
    configure_tool_registration():
        # Load from environment or database
        tool_config = {
            "issuer": "https://bluenote.edu",
            "client_id": PLATFORM_CLIENT_ID,
            "deployment_id": PLATFORM_DEPLOYMENT_ID,
            "oidc_login_url": f"{TOOL_BASE}/lti/oidc_login",
            "launch_url": f"{TOOL_BASE}/lti/launch",
            "redirect_uri": f"{TOOL_BASE}/lti/launch",
            "jwks_url": f"{TOOL_BASE}/lti/.well-known/jwks.json",
            "deep_linking_url": f"{TOOL_BASE}/lti/deep_linking",

            # Tool capabilities
            "scopes": [
                "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly",
                "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
                "https://purl.imsglobal.org/spec/lti-ags/scope/score"
            ],

            # Custom parameters
            "custom_parameters": {
                "confusion_threshold": "$Custom.confusion_threshold",
                "cooldown_minutes": "$Custom.cooldown_minutes"
            }
        }

        # Store in database for multi-tenant support
        await db.upsert_tool_registration(
            platform_url=PLATFORM_ISSUER,
            config=tool_config
        )

        return tool_config

    # ===== OIDC LOGIN IMPLEMENTATION =====
    implement_oidc_login():
        @app.get("/lti/oidc_login")
        @app.post("/lti/oidc_login")
        async def oidc_login(request: Request):
            """
            Handle OIDC login initiation from Moodle
            """
            # Extract parameters
            iss = request.values.get("iss")  # Platform issuer
            login_hint = request.values.get("login_hint")  # User identifier
            target_link_uri = request.values.get("target_link_uri")  # Where to return
            lti_message_hint = request.values.get("lti_message_hint")  # Optional platform data
            client_id = request.values.get("client_id")  # Optional client ID
            deployment_id = request.values.get("lti_deployment_id")  # Deployment ID

            # Validate platform
            if iss != PLATFORM_ISSUER:
                logger.error(f"Unknown platform issuer: {iss}")
                raise HTTPException(400, "Unknown platform")

            # Generate state and nonce
            state = secrets.token_urlsafe(32)
            nonce = secrets.token_urlsafe(32)

            # Store state for validation (with 10 min TTL)
            await token_cache.set(
                f"oidc_state:{state}",
                json.dumps({
                    "nonce": nonce,
                    "login_hint": login_hint,
                    "created_at": datetime.utcnow().isoformat()
                }),
                ttl=600
            )

            # Build authorization redirect URL
            auth_params = {
                "response_type": "id_token",
                "response_mode": "form_post",
                "scope": "openid",
                "client_id": client_id or PLATFORM_CLIENT_ID,
                "redirect_uri": tool_config["redirect_uri"],
                "login_hint": login_hint,
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
            platform_config = await fetch_platform_config(iss)
            auth_url = platform_config["authorization_endpoint"]

            # Redirect to platform for authentication
            redirect_url = f"{auth_url}?{urlencode(auth_params)}"

            logger.info(f"OIDC login initiated for {login_hint} from {iss}")

            return RedirectResponse(url=redirect_url, status_code=302)

    # ===== LAUNCH HANDLER WITH FULL VALIDATION =====
    implement_launch_handler():
        @app.post("/lti/launch")
        async def lti_launch(request: Request):
            """
            Handle LTI 1.3 ResourceLinkRequest launch
            """
            # Extract form data
            id_token = request.form.get("id_token")
            state = request.form.get("state")

            if not id_token or not state:
                raise HTTPException(400, "Missing required parameters")

            # Validate state
            state_data = await token_cache.get(f"oidc_state:{state}")
            if not state_data:
                raise HTTPException(401, "Invalid or expired state")

            state_info = json.loads(state_data)
            await token_cache.delete(f"oidc_state:{state}")

            # Fetch platform's public keys
            platform_keys = await fetch_and_cache_jwks(PLATFORM_JWKS_URL)

            # Decode and verify JWT
            try:
                # Find the right key
                header = jwt.get_unverified_header(id_token)
                key = find_platform_key(platform_keys, header.get("kid"))

                # Verify signature and decode
                claims = jwt.decode(
                    id_token,
                    key=key,
                    algorithms=["RS256"],
                    options={
                        "verify_signature": True,
                        "verify_aud": True,
                        "verify_iss": True,
                        "verify_exp": True,
                        "verify_nbf": True,
                        "verify_iat": True
                    },
                    audience=PLATFORM_CLIENT_ID,
                    issuer=PLATFORM_ISSUER
                )

            except jwt.InvalidTokenError as e:
                logger.error(f"JWT validation failed: {e}")
                raise HTTPException(401, f"Invalid token: {str(e)}")

            # Validate nonce
            if claims.get("nonce") != state_info["nonce"]:
                raise HTTPException(401, "Invalid nonce")

            # Validate LTI version
            lti_version = claims.get("https://purl.imsglobal.org/spec/lti/claim/version")
            if lti_version != "1.3.0":
                raise HTTPException(400, f"Unsupported LTI version: {lti_version}")

            # Validate message type
            message_type = claims.get("https://purl.imsglobal.org/spec/lti/claim/message_type")
            if message_type not in ["LtiResourceLinkRequest", "LtiDeepLinkingRequest"]:
                raise HTTPException(400, f"Unsupported message type: {message_type}")

            # Extract user information
            user_info = {
                "id": claims["sub"],
                "name": claims.get("name", ""),
                "given_name": claims.get("given_name", ""),
                "family_name": claims.get("family_name", ""),
                "email": claims.get("email", ""),
                "roles": claims.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])
            }

            # Extract course context
            context = claims.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
            course_info = {
                "id": context.get("id"),
                "title": context.get("title", ""),
                "label": context.get("label", "")
            }

            # Extract resource link
            resource_link = claims.get("https://purl.imsglobal.org/spec/lti/claim/resource_link", {})

            # Extract platform info
            platform_info = claims.get("https://purl.imsglobal.org/spec/lti/claim/tool_platform", {})

            # Extract service URLs
            services = {
                "nrps": claims.get("https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice", {}),
                "ags": claims.get("https://purl.imsglobal.org/spec/lti-ags/claim/endpoint", {})
            }

            # Store launch data in database
            launch_id = str(uuid4())
            await db.store_launch(
                launch_id=launch_id,
                platform_issuer=claims["iss"],
                deployment_id=claims.get("https://purl.imsglobal.org/spec/lti/claim/deployment_id"),
                user_info=user_info,
                course_info=course_info,
                resource_link=resource_link,
                services=services,
                raw_claims=claims,
                timestamp=datetime.utcnow()
            )

            # Create or update user
            user = await db.upsert_user(
                platform_user_id=user_info["id"],
                email=user_info["email"],
                name=user_info["name"],
                platform_issuer=claims["iss"]
            )

            # Create or update course enrollment
            enrollment = await db.upsert_enrollment(
                user_id=user.id,
                course_id=course_info["id"],
                roles=user_info["roles"],
                course_title=course_info["title"]
            )

            # Handle different message types
            if message_type == "LtiResourceLinkRequest":
                # Standard launch - create session and redirect
                session_data = issue_launch_token(claims, session_signer, token_cache)

                # Determine view based on role
                is_instructor = any(
                    role in str(user_info["roles"])
                    for role in ["Instructor", "Teacher", "Faculty", "Administrator"]
                )

                view = "dashboard" if is_instructor else "student"

                # Build frontend URL
                frontend_url = f"{FRONTEND_BASE}/?token={session_data['token']}&view={view}&course={course_info['id']}"

                # Log successful launch
                await log_launch_event(
                    launch_id=launch_id,
                    user_id=user.id,
                    course_id=course_info["id"],
                    role=view,
                    timestamp=datetime.utcnow()
                )

                return RedirectResponse(url=frontend_url, status_code=302)

            elif message_type == "LtiDeepLinkingRequest":
                # Deep linking request - show resource selection
                return await handle_deep_linking_request(claims, launch_id)

    # ===== DEEP LINKING ENDPOINT =====
    implement_deep_linking_endpoint():
        @app.get("/lti/deep_linking/{launch_id}")
        async def deep_linking_selection(launch_id: str):
            """
            Show resource selection UI for deep linking
            """
            # Retrieve launch data
            launch = await db.get_launch(launch_id)
            if not launch:
                raise HTTPException(404, "Launch not found")

            # Generate selection page HTML
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>BluNote - Select Activity</title>
                <style>
                    body {{ font-family: Arial, sans-serif; padding: 20px; }}
                    .activity {{ border: 1px solid #ddd; padding: 15px; margin: 10px 0; cursor: pointer; }}
                    .activity:hover {{ background: #f0f0f0; }}
                </style>
            </head>
            <body>
                <h1>Select BluNote Activity</h1>
                <div class="activity" onclick="selectActivity('confusion_tracker')">
                    <h3>Confusion Tracker</h3>
                    <p>Real-time confusion monitoring with AI tutoring support</p>
                </div>
                <div class="activity" onclick="selectActivity('analytics_dashboard')">
                    <h3>Analytics Dashboard</h3>
                    <p>Historical confusion patterns and engagement metrics</p>
                </div>

                <script>
                    function selectActivity(type) {{
                        fetch('/lti/deep_linking/{launch_id}/select', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{ activity_type: type }})
                        }}).then(response => response.text())
                          .then(html => document.write(html));
                    }}
                </script>
            </body>
            </html>
            """

            return HTMLResponse(content=html)

        @app.post("/lti/deep_linking/{launch_id}/select")
        async def deep_linking_select(launch_id: str, selection: dict):
            """
            Handle resource selection and return to platform
            """
            # Retrieve launch data
            launch = await db.get_launch(launch_id)
            if not launch:
                raise HTTPException(404, "Launch not found")

            claims = launch["raw_claims"]

            # Get deep linking settings
            dl_settings = claims.get("https://purl.imsglobal.org/spec/lti-dl/claim/deep_linking_settings", {})
            return_url = dl_settings.get("deep_link_return_url")

            if not return_url:
                raise HTTPException(400, "No return URL in deep linking settings")

            # Build content item based on selection
            activity_type = selection.get("activity_type", "confusion_tracker")

            content_item = {
                "type": "ltiResourceLink",
                "title": "BluNote Confusion Tracker" if activity_type == "confusion_tracker" else "BluNote Analytics",
                "text": "Real-time student confusion tracking with AI tutoring",
                "url": f"{TOOL_BASE}/lti/launch",
                "lineItem": {
                    "scoreMaximum": 100,
                    "label": "BluNote Participation",
                    "resourceId": f"bluenote_{activity_type}",
                    "tag": "participation"
                },
                "custom": {
                    "activity_type": activity_type,
                    "confusion_threshold": 25,
                    "cooldown_minutes": 3
                }
            }

            # Create deep linking response JWT
            dl_response_claims = {
                "iss": PLATFORM_CLIENT_ID,
                "aud": claims["iss"],
                "exp": int(time.time()) + 600,
                "iat": int(time.time()),
                "nbf": int(time.time()),
                "nonce": claims.get("nonce"),
                "azp": claims["iss"],
                "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiDeepLinkingResponse",
                "https://purl.imsglobal.org/spec/lti/claim/version": "1.3.0",
                "https://purl.imsglobal.org/spec/lti/claim/deployment_id": claims.get("https://purl.imsglobal.org/spec/lti/claim/deployment_id"),
                "https://purl.imsglobal.org/spec/lti-dl/claim/content_items": [content_item],
                "https://purl.imsglobal.org/spec/lti-dl/claim/data": dl_settings.get("data")
            }

            # Sign with our private key
            dl_jwt = lti_signer.sign(dl_response_claims)

            # Return auto-submit form to platform
            html = f"""
            <!DOCTYPE html>
            <html>
            <body onload="document.forms[0].submit()">
                <form method="POST" action="{return_url}">
                    <input type="hidden" name="JWT" value="{dl_jwt}">
                </form>
            </body>
            </html>
            """

            return HTMLResponse(content=html)

    # ===== PLATFORM CONFIGURATION ENDPOINT =====
    add_platform_config_route():
        @app.get("/lti/config")
        async def get_tool_config(request: Request):
            """
            Return tool configuration for platform registration
            """
            base_url = str(request.url_for("get_tool_config")).replace("/lti/config", "")

            config = {
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

            return JSONResponse(content=config)

        @app.get("/lti/.well-known/jwks.json")
        async def get_jwks():
            """
            Return public keys for JWT verification
            """
            # Generate JWKS from our private key
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            import base64

            # Load private key
            private_key = serialization.load_pem_private_key(
                LTI_PRIVATE_KEY_PEM.encode(),
                password=None,
                backend=default_backend()
            )

            # Get public key
            public_key = private_key.public_key()
            public_numbers = public_key.public_numbers()

            # Convert to JWKS format
            def int_to_base64url(n):
                hex_n = format(n, 'x')
                if len(hex_n) % 2:
                    hex_n = '0' + hex_n
                return base64.urlsafe_b64encode(
                    bytes.fromhex(hex_n)
                ).decode('ascii').rstrip('=')

            jwks = {
                "keys": [
                    {
                        "kty": "RSA",
                        "alg": "RS256",
                        "use": "sig",
                        "kid": LTI_KID,
                        "n": int_to_base64url(public_numbers.n),
                        "e": int_to_base64url(public_numbers.e)
                    }
                ]
            }

            return JSONResponse(content=jwks)

    # ===== HELPER FUNCTIONS =====
    async def fetch_platform_config(issuer: str):
        """Fetch and cache platform configuration"""
        cache_key = f"platform_config:{issuer}"
        cached = await token_cache.get(cache_key)

        if cached:
            return json.loads(cached)

        # Fetch from well-known endpoint
        well_known_url = f"{issuer}/.well-known/openid-configuration"
        response = await http_client.get(well_known_url)
        config = response.json()

        # Cache for 1 hour
        await token_cache.set(cache_key, json.dumps(config), ttl=3600)

        return config

    async def fetch_and_cache_jwks(jwks_url: str):
        """Fetch and cache platform JWKS"""
        cache_key = f"jwks:{jwks_url}"
        cached = await token_cache.get(cache_key)

        if cached:
            return json.loads(cached)

        response = await http_client.get(jwks_url)
        jwks = response.json()

        # Cache for 1 hour
        await token_cache.set(cache_key, json.dumps(jwks), ttl=3600)

        return jwks["keys"]

    def find_platform_key(keys: list, kid: str):
        """Find the right key by kid"""
        for key in keys:
            if key.get("kid") == kid:
                return key
        # If no kid match, try the first key
        return keys[0] if keys else None

    def is_instructor_role(roles: list) -> bool:
        """Check if roles include instructor permissions"""
        instructor_roles = [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
            "http://purl.imsglobal.org/vocab/lis/v2/membership#ContentDeveloper",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Faculty",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Administrator"
        ]
        return any(role in roles for role in instructor_roles)

    def is_student_role(roles: list) -> bool:
        """Check if roles include student permissions"""
        student_roles = [
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Student"
        ]
        return any(role in roles for role in student_roles)

    # ===== EXECUTION =====
    tool_config = configure_tool_registration()
    implement_oidc_login()
    implement_launch_handler()
    implement_deep_linking_endpoint()
    add_platform_config_route()

    logger.info("LTI 1.3 flows implemented")
```

I'll continue with the remaining steps in the next part due to length constraints. Would you like me to continue with Steps 3-10?