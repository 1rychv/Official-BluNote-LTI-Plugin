# Production Tasks Pseudocode

## 1. Harden Authentication & Transport
```pseudo
function secure_services():
    load_env_keys()
    init_jwt_signer(private_key)
    define issue_launch_token(claims):
        token = jwt_sign(claims, expires_in=5_minutes)
        store_token_in_cache(token.id, claims)
        return token

    update_lti_launch_handler():
        claims = verify_incoming_id_token()
        token = issue_launch_token({
            "course_id": claims.course_id,
            "user_id": claims.user_id,
            "roles": claims.roles
        })
        redirect_to_frontend(with_query_token=token.value)

    configure_fastapi_middlewares():
        add_https_redirect()
        add_rate_limiter(global_limit, per_user_limit)
        add_request_logger()

    apply_rest_guard():
        def auth_dependency(request):
            token = extract_bearer_token(request)
            claims = verify_and_cache_lookup(token)
            enforce_course_scope(request, claims)
            return claims

    apply_socket_guard():
        on_socket_connect(payload):
            token = payload.token
            claims = verify_and_cache_lookup(token)
            if claims.invalid: reject_connection()
            attach_claims_to_session(claims)

    set_cors_policy(allowed_origins, allow_credentials=True)
    update_frontend_to_send_token_on_ws_and_http()
```

## 2. Complete LTI Flows (OIDC + Deep Linking)
```pseudo
function complete_lti_flows():
    configure_tool_registration_from_env()

    implement_oidc_login():
        read_platform_metadata()
        compute_state_nonce()
        redirect_user_to_platform_authorize()

    implement_launch_handler():
        fetch_platform_jwks()
        verify_id_token_signature()
        validate_message_type_and_version()
        persist_launch_context(course_id, user_id, roles)
        issue_launch_token()
        respond_with_frontend_redirect()

    implement_deep_linking_endpoint():
        if request.method == GET:
            render_resource_selection_ui()
        if request.method == POST:
            validate_deep_linking_jwt()
            build_content_item_manifest()
            return_deep_linking_response_to_platform()

    add_platform_config_route():
        expose_oidc_login_url()
        expose_launch_url()
        expose_deep_linking_url()
        expose_jwks_url()
```

## 3. Replace In-Memory State with Redis/Postgres
```pseudo
function persist_state():
    init_redis_pool(url=env.REDIS_URL, max_connections=20)
    init_postgres_pool(dsn=env.POSTGRES_DSN, max_size=10)

    run_migrations():
        execute_sql("""
            CREATE TABLE IF NOT EXISTS courses (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT now(),
                roster_override INTEGER,
                threshold_percent INTEGER,
                settings JSONB
            );
        """)
        execute_sql("""
            CREATE TABLE IF NOT EXISTS events (
                id UUID PRIMARY KEY,
                course_id TEXT REFERENCES courses(id),
                user_id TEXT,
                type TEXT,
                payload JSONB,
                occurred_at TIMESTAMP DEFAULT now()
            ) USING timescaledb;
        """)
        execute_sql("""
            CREATE TABLE IF NOT EXISTS tutoring_sessions (
                id UUID PRIMARY KEY,
                course_id TEXT,
                user_id TEXT,
                content JSONB,
                delivered_at TIMESTAMP DEFAULT now()
            );
        """)

    refactor_confused_event_pipeline():
        on_confused(course_id, user_id, ts):
            redis_key = f"presses:{course_id}"
            redis.zadd(redis_key, score=ts, member=user_id)
            redis.expire(redis_key, WINDOW_SEC * 2)
            postgres.insert(events, {course_id, user_id, type="confused", occurred_at=ts})

    compute_metrics(course_id, now):
        redis_key = f"presses:{course_id}"
        redis.zremrangebyscore(redis_key, -inf, now - WINDOW_MS)
        unique_count = redis.zcard(redis_key)
        roster = fetch_roster_size(course_id)
        tutoring_count = postgres.count_recent(tutoring_sessions, course_id, window=WINDOW_MS)
        return { unique_count, roster, tutoring_count }

    fetch_roster_size(course_id):
        auto = redis.get(f"presence:{course_id}")
        if auto and auto >= MIN_AUTO_ROSTER:
            return auto
        row = postgres.select_one("SELECT roster_override FROM courses WHERE id = $1", course_id)
        return row.roster_override or DEFAULT_ROSTER

    update_roster_override(course_id, roster):
        postgres.upsert(courses, {id: course_id, roster_override: roster})

    persist_tutoring(course_id, user_id, content):
        postgres.insert(tutoring_sessions, {course_id, user_id, content})
        if content.includes_assets:
            s3.upload(content.assets)
        redis.set(f"tutoring:{user_id}", json(content), ttl=TUTORING_TTL)
```

## 4. Build NRPS & AGS Integrations
```pseudo
function integrate_platform_services():
    load_platform_service_credentials()
    validate_required_scopes([NRPS_SCOPE, AGS_SCOPE?])

    sync_nrps_members(course_id, launch_claims):
        nrps_url = launch_claims.nrps_context_url
        if not nrps_url:
            return
        token = fetch_service_token(scope=NRPS_SCOPE)
        members_response = http_get(nrps_url, headers={"Authorization": f"Bearer {token}"})
        for member in members_response.members:
            postgres.upsert(members_table, map_member_to_row(member))
        redis.set(f"roster:{course_id}", len(members_response.members), ttl=ROSTER_CACHE_TTL)

    refresh_nrps_job():
        schedule_cron(every=30_minutes, task=sync_all_courses)
        def sync_all_courses():
            for course in postgres.select("SELECT id FROM courses"):
                sync_nrps_members(course.id, lookup_launch_claims(course.id))

    ensure_line_item(course_id, label):
        token = fetch_service_token(scope=AGS_SCOPE)
        line_items_url = launch_claims.lineitems_url
        existing = http_get(line_items_url, headers=bearer(token))
        if not find_line_item(existing, label):
            http_post(line_items_url, body=build_line_item(label), headers=bearer(token))

    submit_score(course_id, user_id, score):
        token = fetch_service_token(scope=AGS_SCOPE)
        line_item = ensure_line_item(course_id, label="BluNote Participation")
        payload = build_score_document(user_id, score)
        http_post(line_item.scores_url, body=payload, headers=bearer(token))

    integrate_metrics_with_nrps(course_id):
        roster_size = redis.get(f"roster:{course_id}") or count_members_in_db(course_id)
        presence = redis.zcard(f"presence:{course_id}")
        return max(roster_size, presence)
```

## 5. Productionize Tutoring Orchestrator
```pseudo
function productionize_tutoring():
    load_provider_config(llm_api_key, model_name, fallback_model)
    create_prompt_templates()

    gather_context(course_id):
        slides = postgres.select_recent(slides_table, course_id, limit=5)
        confusion_events = postgres.select_recent(events, course_id, type="confused", window=15_minutes)
        instructor_notes = postgres.select_recent(notes_table, course_id)
        return {slides, confusion_events, instructor_notes}

    generate_tutoring_content(user_id, context):
        prompt = render_template("tutoring_prompt", context)
        response = call_llm(model_name, prompt, temperature=0.3)
        if response.error:
            response = call_llm(fallback_model, prompt)
        structured = parse_llm_response(response.text)
        validate_structured_content(structured)
        postgres.insert(tutoring_sessions, {user_id, course_id=context.course_id, content: structured})
        enqueue_background_job(upload_assets, structured.assets)
        return structured

    upload_assets(assets):
        for asset in assets:
            s3.put_object(bucket=TUTORING_BUCKET, key=asset.key, body=asset.blob, metadata=asset.meta)

    deliver_content(course_id, confused_users):
        context = gather_context(course_id)
        for user_id in confused_users:
            content = generate_tutoring_content(user_id, context)
            socket.emit_to_user(user_id, event="tutoring", payload=content)
        record_trigger_event(course_id, confused_users)

    record_trigger_event(course_id, users):
        postgres.insert(events, {course_id, type="tutoring_trigger", payload={users}})
```

## 6. Add Observability & Safeguards
```pseudo
function add_observability():
    configure_structured_logger(service_name="blunote-api", sink=stdout)
    add_request_id_middleware()
    wrap_fastapi_with_logging_middleware(mask_pii=True)
    log_socket_events(events=["connect", "disconnect", "trigger", "tutoring"])

    integrate_metrics():
        install_prometheus_instrumentator()
        instrument_fastapi_routes(histogram_buckets=[0.1,0.25,0.5,1,2,5])
        instrument_redis_client()
        instrument_postgres_pool()
        emit_custom_gauges(confusion_pct, connected_users)

    add_security_headers():
        set_csp(policy="default-src 'self'; connect-src 'self' wss://*")
        enable_hsts(max_age=31536000)
        set_referrer_policy("strict-origin-when-cross-origin")
        enable_x_xss_protection()

    implement_error_handling():
        register_exception_handler(Exception, log_and_return_500)
        integrate_sentry_or_equivalent(dsn=env.SENTRY_DSN)
        add_validation_error_handler(return_422_json)

    run_pen_test_checklist():
        execute_dependency_audit()
        run_security_scans(bandit, npm_audit)
        document_findings()
```

## 7. Create Dev/Prod Tooling & Deployment
```pseudo
function build_tooling():
    write_docker_compose():
        services = {
            api: {build: "app/server-py", env_file: ".env", ports: ["4000:4000"], depends_on: [redis, postgres]},
            web: {build: "app/web", ports: ["5173:5173"], env_file: "app/web/.env", depends_on: [api]},
            redis: {image: "redis:7", ports: ["6379:6379"]},
            postgres: {image: "postgres:15", ports: ["5432:5432"], environment: postgres_env, volumes: ["pgdata:/var/lib/postgresql/data"]}
        }
        write_yaml("docker-compose.yml", services)

    add_make_targets():
        makefile.add_target("setup", cmds=["python -m venv", "pip install -r", "npm install"])
        makefile.add_target("dev", cmds=["docker-compose up"])
        makefile.add_target("test", cmds=["pytest", "npm test"])
        makefile.add_target("deploy", cmds=["terraform apply", "helm upgrade"])

    configure_ci_pipeline():
        workflow = github_actions()
        workflow.add_job("lint", steps=[checkout(), setup_python(), install_backend_deps(), run_flake8(), run_eslint()])
        workflow.add_job("test", needs="lint", steps=[checkout(), setup_services_with_docker(), run_pytest(), run_npm_test()])
        workflow.add_job("build", needs="test", steps=[build_frontend(), build_backend_image()])
        workflow.add_job("deploy", needs="build", if=on_main_branch, steps=[assume_role(), helm_deploy()])

    create_infrastructure_scripts():
        terraform.define_module("network", resources=[vpc, subnets, security_groups])
        terraform.define_module("database", resources=[aurora_postgres, parameter_groups])
        terraform.define_module("cache", resources=[elasticache_redis])
        terraform.define_module("secrets", resources=[secrets_manager_entries])
        terraform.define_module("app", resources=[ecs_service_or_kubernetes])
        scripts/provision.sh orchestrates apply order
```

## 8. Expand Instructor UX & Analytics
```pseudo
function enhance_instructor_dashboard():
    design_pause_acknowledge_flow():
        map_user_story("Instructor acknowledges alert and logs explanation")
        define API schema {action: "pause", note: string, acknowledged_by: user}

    extend_backend_actions_api():
        create_route(POST, "/api/course/{id}/actions", auth=token)
        validate_payload_against_schema()
        write_action_to_events_table()
        broadcast_action_over_socket()

    implement_frontend_components():
        add `AlertHistoryPanel` component pulling `/api/course/{id}/events?action`
        add `AcknowledgeModal` with textarea + submit button
        wire submit to POST action, optimistic update state
        display acknowledgment timestamp + instructor name in list

    build_analytics_views():
        backend_query = "SELECT date_trunc('minute', occurred_at) AS bucket, COUNT(*) FROM events WHERE type='confused' GROUP BY bucket ORDER BY bucket"
        convert_query_results_to_series()
        expose endpoint `/api/course/{id}/analytics/confusion`
        frontend fetches series and renders area chart (e.g., Recharts)
        add filters by timeframe (10, 30, 60 minutes)

    add_empty_states_and_accessibility():
        show friendly message when no alerts yet
        ensure components announce updates via aria-live regions
```

## 9. Validate UX, Accessibility, and Testing
```pseudo
function finalize_user_experience():
    run_accessibility_audit():
        execute_axe_on_frontend()
        address reported violations
        add unit tests asserting aria-label presence

    strengthen_keyboard_support():
        ensure tab order on modal/dialog controls
        add keyboard shortcuts for instructor acknowledge
        write Cypress test to navigate UI using keyboard only

    implement_i18n_framework():
        integrate i18next (frontend) and gettext (backend)
        wrap UI strings with translation function
        load locale files based on user preference from LTI launch
        add fallback to English if translation missing

    expand_testing():
        backend: add pytest modules for metrics math, LTI validation, token guards
        backend: add async integration test using TestClient + redis/postgres fixtures
        frontend: write React Testing Library tests for Student/Instructor flows
        e2e: add Playwright spec launching dev server, simulating two students + instructor scenario
        integrate test suite into CI pipeline jobs

    document QA checklist():
        include cross-browser matrix
        include responsive breakpoints verification
```

## 10. Monitoring, Alerting, and Pilot Rollout
```pseudo
function launch_pilot():
    provision_monitoring():
        deploy Prometheus + Grafana dashboards for API latency, socket connections, LTI failures
        configure log shipping to centralized store (e.g., CloudWatch, ELK)

    configure_alerting():
        define alerts for error_rate > 2%, websocket_disconnect_spike, redis_latency
        route critical alerts to on-call Slack channel + PagerDuty

    create_runbook():
        document how to restart services, roll back deploy, contact platform admins
        include dashboard URLs and log query templates
        store runbook in shared knowledge base

    execute_pilot():
        select pilot institutions and courses
        schedule onboarding sessions and collect consent
        enable feature flags for pilot users
        monitor dashboards daily, log pilot incidents
        hold weekly review to capture feedback and prioritize fixes

    plan_general_availability():
        synthesize pilot feedback into backlog
        update documentation and support materials
        confirm scalability tests pass
```
