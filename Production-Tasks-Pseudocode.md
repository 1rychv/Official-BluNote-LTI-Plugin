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
    init_redis_pool()
    init_postgres_pool()

    create_tables():
        execute_sql("""
            CREATE TABLE courses(...);
            CREATE TABLE events(...);
            CREATE TABLE tutoring_sessions(...);
        """)

    refactor_metrics():
        when_confused_event(course_id, user_id):
            redis.add_to_sorted_set(key=presses:course_id, member=user_id, score=timestamp)
            postgres.insert_event(course_id, user_id, type="confused", ts)

        compute_metrics():
            redis.zremrangebyscore(old_events)
            unique_count = redis.zcard(current_window)
            roster = query_roster(course_id)
            return { unique_count, roster }

    store_roster_override():
        postgres.upsert_roster(course_id, roster_size)

    persist_tutoring_content():
        postgres.insert_tutoring(course_id, user_id, payload)
        s3.upload_if_needed(payload)
```

## 4. Build NRPS & AGS Integrations
```pseudo
function integrate_platform_services():
    ensure_nrps_credentials()
    ensure_ags_credentials()

    implement_nrps_client():
        token = fetch_service_token(scope_nrps)
        roster = http_get(platform_nrps_url, headers=token)
        cache_roster_in_postgres(roster)

    update_metrics_pipeline():
        roster_size = active_connections or roster_from_db
        include_presence_heartbeat_updates()

    implement_optional_ags():
        token = fetch_service_token(scope_ags)
        ensure_line_item_exists(course_id)
        on_tutoring_completion(user_id):
            score_payload = build_score_document()
            http_post(line_item_url + "/scores", payload, headers=token)
```

## 5. Productionize Tutoring Orchestrator
```pseudo
function productionize_tutoring():
    load_provider_config(llm_api_key, model_name)

    gather_context(course_id):
        fetch_recent_slides()
        fetch_recent_questions()

    generate_tutoring_content(user_id, context):
        prompt = build_prompt(context)
        llm_response = call_llm_api(prompt)
        structured = parse_response(llm_response)
        store_in_postgres(structured)
        upload_assets_to_s3(structured)
        return structured

    deliver_content():
        on_trigger(course_id):
            for user in confused_users:
                content = generate_tutoring_content(user, context)
                emit_over_socket(user, content)
```

## 6. Add Observability & Safeguards
```pseudo
function add_observability():
    configure_structured_logger(service_name)
    wrap_fastapi_with_logging_middleware()
    emit_event_logs_for_socket_actions()

    integrate_metrics():
        expose_prometheus_endpoint()
        instrument_redis_and_postgres_clients()
        record_latency_histograms()

    add_security_headers():
        apply_csp_policy()
        set_hsts()
        enable_referrer_policy()

    implement_error_handling():
        add_global_exception_handler()
        capture_exceptions_to_monitoring()
```

## 7. Create Dev/Prod Tooling & Deployment
```pseudo
function build_tooling():
    write_docker_compose():
        define services: api, web, redis, postgres
        mount volumes for local dev data

    add_make_targets():
        make setup
        make dev
        make test
        make deploy

    configure_ci_pipeline():
        steps = [lint, test, build, package, deploy]
        run_on_pull_request()

    create_infrastructure_scripts():
        terraform_or_cloudformation()
        provision_database()
        provision_cache()
        configure_secrets_manager()
        set_up_cdn_for_frontend()
```

## 8. Expand Instructor UX & Analytics
```pseudo
function enhance_instructor_dashboard():
    design_pause_acknowledge_flow()
    add_api_endpoint("/api/course/{id}/actions")

    implement_frontend_components():
        add_alert_history_panel()
        add_acknowledge_button()
        wire_button_to_post_action()

    build_analytics_views():
        backend: query events table for time series
        aggregate_by_interval()
        send data to frontend
        frontend: render chart component
```

## 9. Validate UX, Accessibility, and Testing
```pseudo
function finalize_user_experience():
    run_accessibility_audit()
    add_keyboard_focus_states()
    ensure_color_contrast_meets_wcag()

    implement_i18n_framework():
        extract_strings_to_translation_files()
        load_locale_from_user_pref()

    expand_testing():
        add_backend_unit_tests()
        add_integration_tests_with_testcontainers()
        add_frontend_e2e_tests(playwright)
        integrate_tests_into_ci()
```

## 10. Monitoring, Alerting, and Pilot Rollout
```pseudo
function launch_pilot():
    set_up_monitoring_dashboards(grafana)
    configure_alert_rules(latency, error_rate, socket_disconnects)

    create_runbook():
        document incident response steps
        include contact rotation

    execute_pilot():
        onboard limited instructors
        track metrics
        collect feedback
        iterate_on_findings()
```
