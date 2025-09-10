# BluNote (MVP Dev Build)

This is a minimal implementation of the BluNote web app per SPEC-1 — focusing on the realtime confusion button, instructor dashboard, threshold logic, and a tutoring stub. By request, the backend is provided in Python (FastAPI + Socket.IO). LTI 1.3 endpoints are not included in this dev build; integrate them later per the specs.

## Stack
- Backend: Node.js (Express + Socket.IO)
- Frontend: React (Vite)
- State: In-memory (dev). Replace with Redis/Postgres for prod.

## Run locally
Prerequisites: Node 18+ and npm; Python 3.10+

1. Start the backend (Python)
```
cd "BluNote LTI/app/server-py"
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 4000 --reload
```

2. Start the frontend (React)
```
cd "BluNote LTI/app/web"
npm install
npm run dev
```

3. Open the app:
- http://localhost:5173
- Use the quick links on the home page to open two students and one instructor for the same course (e.g., COURSE1).

## Features implemented
- Student “I’m Confused” button with 20s debounce.
- Rolling 120s window; percent confusion = unique students in window / roster.
- Instructor dashboard shows live metrics and alerts when threshold (default 25%) is exceeded.
- Cooldown of 3 minutes between triggers per course.
- Tutoring stub: when threshold triggers, each confused student receives a simple tutoring panel pushed via WebSocket.
 - Auto roster: denominator auto-uses active connected students within the window; falls back to manual roster if no presence is detected (e.g., testing with only instructor open).

## Configuration
Backend env (`server-py/.env`):
- `PORT` (default 4000)
- `ALLOWED_ORIGIN` (default http://localhost:5173)
- `THRESHOLD_PERCENT` (default 25)
- `WINDOW_SEC` (default 120)
- `DEBOUNCE_SEC` (default 20)
- `COOLDOWN_SEC` (default 180)

Frontend env (`web/.env` optional):
- `VITE_API_BASE` (default http://localhost:4000)

## Next steps (per SPEC)
- Add LTI 1.3 endpoints (OIDC login + Launch) using `pylti1p3` in a dedicated Gateway service.
- Replace in-memory state with Redis for counters and Postgres for events/users.
- Implement NRPS (roster) and AGS (grade passback) adapters.
- Add Tutoring Orchestrator with real LLM calls (opt-in via API key).
- Harden security (CSP, JWT validation, JWKS); add audit logging and metrics dashboards.
