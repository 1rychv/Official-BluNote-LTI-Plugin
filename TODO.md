# BluNote TODO

I will keep this list updated and cross out items as they’re completed. Items are ordered by priority where relevant.

## Completed
- [x] ~~Initialize repo, push `main`, create `development` branch.~~
- [x] ~~FastAPI + Socket.IO backend with confusion press, debounce (20s), window W=120s, cooldown 3m.~~
- [x] ~~React (Vite) frontend: Student and Instructor pages.~~
- [x] ~~Realtime metrics broadcast; tutoring stub delivered on trigger (≥ 25%).~~
- [x] ~~Presence-based auto roster with manual override fallback.~~
- [x] ~~UI pass (dark theme, cards, meter with threshold marker, connection badges, student cooldown).~~
- [x] ~~Fix meter label overlap/positioning.~~
- [x] ~~LTI gateway scaffold: JWKS, OIDC login, Launch, config, and `/lti/dev/launch`.~~

## In Progress
- [ ] Secure WebSocket and REST with short‑lived tokens minted from LTI launch (per course/user scope).

## Next Up
- [ ] Deep Linking endpoint (pylti1p3) + tool registration config for Moodle.
- [ ] NRPS: fetch roster; compute active denominator using presence within W; fall back intelligently.
- [ ] Redis for counters; Postgres for events/users (schemas, migrations).
- [ ] Instructor actions: “Pause & Explain” acknowledgement; action log on triggers; visual state in UI.
- [ ] Tutoring Orchestrator (LLM call, S3 artifacts, completion tracking) and optional AGS grade passback.
- [ ] Observability/security: structured logs, metrics, CSP, error boundaries.
- [ ] Docker Compose for dev (backend, web, Redis, Postgres) + docs.

## Backlog / Nice‑to‑Have
- [ ] Analytics views: confusion timeline, exportable session report.
- [ ] Localization + accessibility polish.
- [ ] Admin panel for multi‑institution settings and privacy controls.

## How to Validate Current Build
- Backend: `cd "BluNote LTI/app/server-py" && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 4000 --reload`
- Frontend: `cd "BluNote LTI/app/web" && npm run dev` → open `http://localhost:5173`
- Open two students + one instructor for the same `courseId`. Instructor dashboard should show “Roster (auto)”. Reduce roster to test thresholds quickly.
- Dev LTI launch (no Moodle):
  - Instructor: `http://localhost:4000/lti/dev/launch?role=instructor&courseId=COURSE1&name=Prof`
  - Student: `http://localhost:4000/lti/dev/launch?role=student&courseId=COURSE1&userId=dev1&name=Alice`

---

## Your Next Actions
1) Run both services and open 2 students + 1 instructor to verify “Roster (auto)” reflects active connections.
2) Trigger an alert by setting roster small (e.g., 4) and pressing “I’m Confused” from one student.
3) Confirm tutoring stub appears for the confused student.
4) If you want to test a dev “launch”, visit `/lti/dev/launch` URLs above.

When you’re ready, I’ll implement token‑secured WS + Deep Linking next.

