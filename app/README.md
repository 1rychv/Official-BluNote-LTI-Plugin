# BluNote (MVP Dev Build)

This is a minimal implementation of the BluNote web app per SPEC-1 — focusing on the realtime confusion button, instructor dashboard, threshold logic, and a tutoring stub. By request, the backend is provided in Python (FastAPI + Socket.IO). An initial LTI 1.3 gateway (OIDC + Launch scaffold, JWKS, config, and a dev launch route) is included for wiring and testing.

## Stack
- Backend: Python (FastAPI + Socket.IO)
- Frontend: React (Vite)
- State: In-memory (dev). Replace with Redis/Postgres for prod.

## Run locally
Prerequisites: Python 3.10+; Node 18+ and npm (frontend tooling)

All paths below are relative to the repo root (`BluNote LTI/`).

1. Backend setup (first run)
```
# from repo root
cd app/server-py
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
deactivate
cd ../..
```

2. Frontend setup (first run)
```
# from repo root
cd app/web
npm install
cd ../..
```

3. Start both services
```
# from repo root
cd app
./dev.sh
```

4. Open the app:
- http://localhost:5173
- Use the quick links on the home page to open two students and one instructor for the same course (e.g., COURSE1).

## Features implemented
- Student “I’m Confused” button with 20s debounce.
- Rolling 120s window; percent confusion = unique students in window / roster.
- Instructor dashboard shows live metrics and alerts when threshold (default 25%) is exceeded.
- Cooldown of 3 minutes between triggers per course.
- Tutoring stub: when threshold triggers, each confused student receives a simple tutoring panel pushed via WebSocket.
- Auto roster: denominator auto-uses active connected students within the window; falls back to manual roster if no presence is detected (e.g., testing with only instructor open).
- LTI gateway (scaffold): `/lti/oidc_login`, `/lti/launch`, `/lti/.well-known/jwks.json`, `/lti/config`, and `/lti/dev/launch` for local testing without Moodle.

## Configuration
Backend env (`server-py/.env`):
- `PORT` (default 4000)
- `ALLOWED_ORIGIN` (default http://localhost:5173)
- `THRESHOLD_PERCENT` (default 25)
- `WINDOW_SEC` (default 120)
- `DEBOUNCE_SEC` (default 20)
- `COOLDOWN_SEC` (default 180)

LTI gateway env (optional now):
- `FRONTEND_BASE` (default `ALLOWED_ORIGIN`)
- `PLATFORM_ISSUER`, `PLATFORM_CLIENT_ID`, `PLATFORM_AUTH_LOGIN_URL`, `PLATFORM_JWKS_URL`, `PLATFORM_DEPLOYMENT_ID`
- `TOOL_REDIRECT_URI` (default `http://localhost:4000/lti/launch`)
- `LTI_PRIVATE_KEY_PEM`, `LTI_KID` (if not set, a dev key is generated for JWKS)

Frontend env (`web/.env` optional):
- `VITE_API_BASE` (default http://localhost:4000)

## Next steps (per SPEC)
- Complete LTI 1.3 flows (OIDC + Launch + Deep Linking) using `pylti1p3`, wired to real Moodle config.
- Replace in-memory state with Redis for counters and Postgres for events/users.
- Implement NRPS (roster) and AGS (grade passback) adapters.
- Add Tutoring Orchestrator with real LLM calls (opt-in via API key).
- Harden security (CSP, JWT validation, JWKS); add audit logging and metrics dashboards.
