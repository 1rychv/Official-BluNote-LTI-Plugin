# SPEC-1-LTI Plugin

## Implementation

### Technology stack (MVP)
- **Backend**: Node.js (Express + LTI 1.3 libraries like `ltijs`), or Python (FastAPI + `pylti1p3`)
- **Frontend**: React (embedded via LTI launch iframe inside Moodle)
- **Realtime**: WebSockets via Socket.IO (Node.js) or FastAPI websockets; backed by Redis Pub/Sub
- **Database**: PostgreSQL (persistent entities), Redis (ephemeral counters, windowed confusion events)
- **Storage**: S3-compatible store (AWS S3, MinIO, or GCP Cloud Storage) for tutoring artifacts
- **AI tutoring**: External LLM API (OpenAI, Anthropic) integrated via REST
- **Containerization**: Docker; orchestrated via Kubernetes or simpler managed service (ECS/GKE)
- **Auth & Security**: OAuth2/OIDC per LTI 1.3 spec, JWT validation, JWKS endpoints from Moodle
- **Deployment**: Cloud (AWS/GCP/Azure), with TLS termination via Nginx or API Gateway

### Moodle integration steps
1. **Register the BluNote Tool in Moodle**
   - In Moodle, go to *Site Administration → Plugins → External Tools → Manage tools*
   - Add new tool → provide *Tool URL, Initiate login URL, Redirect URL, JWKS URL*
   - Copy Moodle’s *client_id*, *deployment_id*, and *issuer* into BluNote config

2. **Implement LTI 1.3 endpoints**
   - **OIDC Login Initiation**: handles login initiation from Moodle
   - **LTI Launch (ResourceLinkRequest)**: verifies JWT, creates/updates user/course/session
   - **Deep Linking endpoint**: allows instructor to select BluNote activity
   - **NRPS endpoint**: retrieves course roster (if enabled)
   - **AGS endpoint**: posts grades/participation back to Moodle

3. **Configure course tool placement**
   - Instructor adds BluNote tool into course (Deep Linking)
   - Students see “BluNote” link in course menu

4. **Test flows**
   - Launch as instructor → verify dashboard opens
   - Launch as student → verify confusion button works, websocket channel connects
   - Confirm roster sync (NRPS) and grade push (AGS) work end-to-end

### Realtime system deployment
- **WebSocket service** behind load balancer with sticky sessions or token-based channel assignment
- **Redis cluster** for Pub/Sub and sliding-window counters
- Horizontal scaling: multiple WebSocket pods; Redis manages shared state
- Monitoring latency <200ms per event → instructor dashboard

### Event flow implementation
- **Student “Confused” press**:
  1. POST → Realtime API → validated token
  2. Publish event to Redis stream with {course, user, ts}
  3. Rules Service increments rolling counter in Redis ZSET
  4. If threshold exceeded, publish Trigger event to course channel
  5. WebSocket service pushes to instructor dashboard clients

- **Tutoring orchestration**:
  1. Trigger event generates tutoring session entries in DB
  2. Orchestrator builds prompt context (slide ID, topic tags)
  3. Calls external LLM API → receives explanation + practice Qs
  4. Stores artifacts (text, quiz JSON, optional media) in S3
  5. Student notified via BluNote UI (link to tutoring panel)

- **Participation credit**:
  - Once tutoring session is completed, AGS score POSTed back to Moodle with `{activityProgress: "Completed", gradingProgress: "FullyGraded", score: 1.0}`

### Security hardening
- Rotate private keys for each deployment (kid in JWKS)
- Use HTTPS only; set CSP headers for iframe embedding in Moodle
- Store only hashed identifiers where possible (e.g., email_hash)
- FERPA/GDPR compliance: configurable retention periods, delete-on-request
- Audit logs: record LTI launches, confusion triggers, tutoring completions

### Observability
- Metrics: latency (press-to-dashboard), tutoring API latency, websocket connection counts
- Logs: structured JSON, per-course correlation IDs
- Alerts: threshold triggers, websocket disconnect spikes
- Dashboards: Grafana or DataDog

### Deployment strategy
- **Phase 1**: Pilot at one university (Moodle instance) with 1–2 courses
- **Phase 2**: Extend to whole department; add analytics features
- **Phase 3**: Multi-university; add scaling, dashboards, billing

