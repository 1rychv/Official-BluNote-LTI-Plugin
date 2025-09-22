import os
import time
from typing import Dict, Any, List, Set
from urllib.parse import parse_qs

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import socketio


load_dotenv()

PORT = int(os.getenv("PORT", "4000"))
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:5173")
FRONTEND_BASE = os.getenv("FRONTEND_BASE") or ALLOWED_ORIGIN

THRESHOLD_PERCENT = int(os.getenv("THRESHOLD_PERCENT", "25"))
WINDOW_MS = 1000 * int(os.getenv("WINDOW_SEC", "120"))
DEBOUNCE_MS = 1000 * int(os.getenv("DEBOUNCE_SEC", "20"))
COOLDOWN_MS = 1000 * int(os.getenv("COOLDOWN_SEC", "180"))


fastapi_app = FastAPI()
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=[ALLOWED_ORIGIN])


class CourseState:
    def __init__(self) -> None:
        self.roster: int = 20
        self.presses: List[Dict[str, Any]] = []  # {userId, ts}
        self.lastPressByUser: Dict[str, float] = {}
        self.lastTriggerAt: float = 0.0
        self.tutoringByUser: Dict[str, Dict[str, Any]] = {}
        self.presenceByUser: Dict[str, float] = {}  # student userId -> lastSeenMs


class AppState:
    def __init__(self) -> None:
        self.courses: Dict[str, CourseState] = {}
        self.socketsByUser: Dict[str, Set[str]] = {}


state = AppState()


def get_course(course_id: str) -> CourseState:
    if course_id not in state.courses:
        state.courses[course_id] = CourseState()
    return state.courses[course_id]


def prune_old(course: CourseState, now_ms: float) -> None:
    horizon = now_ms - WINDOW_MS
    # Keep only events inside window
    course.presses = [e for e in course.presses if e['ts'] >= horizon]


def unique_users_in_window(course: CourseState) -> Set[str]:
    return {e['userId'] for e in course.presses}


def compute_metrics(course_id: str) -> Dict[str, Any]:
    course = get_course(course_id)
    now_ms = time.time() * 1000
    prune_old(course, now_ms)
    users = unique_users_in_window(course)
    unique_count = len(users)
    # Auto roster based on presence in last WINDOW_MS
    auto_count = sum(1 for ts in course.presenceByUser.values() if (now_ms - ts) <= WINDOW_MS)
    roster_source = 'auto' if auto_count > 0 else 'manual'
    base_roster = auto_count if auto_count > 0 else int(course.roster or 0)
    roster = max(1, base_roster)  # avoid divide by zero
    pct = round((unique_count / roster) * 100)
    return {
        "uniqueCount": unique_count,
        "roster": roster,
        "pct": pct,
        "windowSec": WINDOW_MS // 1000,
        "debounceSec": DEBOUNCE_MS // 1000,
        "rosterSource": roster_source,
    }


def generate_tutoring_stub(course_id: str) -> Dict[str, Any]:
    return {
        "courseId": course_id,
        "title": "Quick Explanation",
        "text": "It looks like several students are confused. Here is a concise recap: Focus on key concept X, break it into steps A→B→C, and practice with the example below.",
        "practice": [
            {"q": "Explain concept X in your own words.", "a": "Free response"},
            {"q": "Which step comes after B?", "a": "C"},
        ],
    }


async def maybe_trigger(course_id: str) -> None:
    course = get_course(course_id)
    now_ms = time.time() * 1000
    metrics = compute_metrics(course_id)
    if metrics['pct'] >= THRESHOLD_PERCENT and (now_ms - course.lastTriggerAt) >= COOLDOWN_MS:
        course.lastTriggerAt = now_ms
        confused_users = list(unique_users_in_window(course))
        # Tutoring push
        for user_id in confused_users:
            content = generate_tutoring_stub(course_id)
            course.tutoringByUser[user_id] = content
            # Notify connected sockets for this user
            for sid in list(state.socketsByUser.get(user_id, set())):
                await sio.emit('tutoring', content, to=sid)

        await sio.emit('trigger', {
            'courseId': course_id,
            'pct': metrics['pct'],
            'threshold': THRESHOLD_PERCENT,
            'at': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()),
        }, room=f'course:{course_id}')


def record_press(course_id: str, user_id: str) -> bool:
    course = get_course(course_id)
    now_ms = time.time() * 1000
    last = course.lastPressByUser.get(user_id, 0)
    if (now_ms - last) < DEBOUNCE_MS:
        return False
    course.lastPressByUser[user_id] = now_ms
    course.presses.append({"userId": user_id, "ts": now_ms})
    prune_old(course, now_ms)
    return True


@fastapi_app.get('/health')
async def health():
    return {"ok": True, "time": time.strftime('%Y-%m-%dT%H:%M:%S')}


# API routes moved to app.api.routes module with authentication


# Connection handlers moved to connect_authenticated below


# Store sid -> (course_id, user_id, role)
sid_map: Dict[str, Dict[str, str]] = {}


@sio.event
async def disconnect(sid):
    info = sid_map.pop(sid, None)
    if info:
        user_id = info.get('user_id')
        course_id = info.get('course_id')
        role = info.get('role')
        if user_id in state.socketsByUser:
            state.socketsByUser[user_id].discard(sid)
            if not state.socketsByUser[user_id]:
                del state.socketsByUser[user_id]
                # If this user has no more sockets, clear presence (will also naturally drop after window)
                if role == 'student' and course_id:
                    course = get_course(course_id)
                    course.presenceByUser.pop(user_id, None)


# Override connect to use proper authentication
@sio.on('connect')
async def connect_authenticated(sid, environ, auth):
    from app.lti.websocket_auth import websocket_auth

    # Try token-based authentication first
    if auth:
        claims = await websocket_auth.authenticate_connection(auth)
        if claims:
            session_data = websocket_auth.create_session_data(claims)
            sid_map[sid] = session_data

            # Join appropriate rooms
            rooms = websocket_auth.get_room_names(claims)
            for room in rooms:
                await sio.enter_room(sid, room)

            # Track socket by user for tutoring pushes
            user_id = claims["user_id"]
            state.socketsByUser.setdefault(user_id, set()).add(sid)

            # Update presence for students
            if claims.get("is_student", False):
                course = get_course(claims["course_id"])
                course.presenceByUser[user_id] = time.time() * 1000

            # Send initial metrics
            metrics = {
                "courseId": claims["course_id"],
                "threshold": THRESHOLD_PERCENT,
                **compute_metrics(claims["course_id"])
            }
            await sio.emit('metrics', metrics, to=sid)
            return True

    # Fallback to query string authentication for development
    query = parse_qs(environ.get('QUERY_STRING', ''))
    course_id = (query.get('courseId', [None])[0] or 'course-dev')
    user_id = (query.get('userId', [None])[0] or f'user-{sid}')
    role = (query.get('role', [None])[0] or 'student')

    sid_map[sid] = {
        "course_id": course_id,
        "user_id": user_id,
        "role": role,
        "is_instructor": role == 'instructor',
        "is_student": role == 'student'
    }

    # Track socket by user
    state.socketsByUser.setdefault(user_id, set()).add(sid)

    # Join course room
    await sio.enter_room(sid, f'course:{course_id}')

    # Update presence for students
    if role == 'student':
        course = get_course(course_id)
        course.presenceByUser[user_id] = time.time() * 1000

    # Send initial metrics
    metrics = {"courseId": course_id, "threshold": THRESHOLD_PERCENT, **compute_metrics(course_id)}
    await sio.emit('metrics', metrics, to=sid)

    return True


@sio.on('confused')
async def on_confused(sid):
    info = sid_map.get(sid)
    if not info:
        await sio.emit('error', {'message': 'Unauthorized'}, to=sid)
        return

    # Check if user is a student
    if not info.get('is_student', False):
        await sio.emit('error', {'message': 'Only students can report confusion'}, to=sid)
        return

    course_id = info.get('course_id', 'course-dev')
    user_id = info.get('user_id', f'user-{sid}')

    # Record confusion press
    accepted = record_press(course_id, user_id)

    # Update presence when interacting
    course = get_course(course_id)
    course.presenceByUser[user_id] = time.time() * 1000

    # Broadcast updated metrics
    metrics = {"courseId": course_id, "threshold": THRESHOLD_PERCENT, **compute_metrics(course_id)}
    await sio.emit('metrics', metrics, room=f'course:{course_id}')

    # Check for trigger
    if accepted:
        await maybe_trigger(course_id)

    # Send response to user
    await sio.emit('confused_response', {'status': 'recorded', 'accepted': accepted}, to=sid)


@sio.on('presence')
async def on_presence(sid):
    info = sid_map.get(sid) or {}
    course_id = info.get('course_id', 'course-dev')
    user_id = info.get('user_id', f'user-{sid}')
    course = get_course(course_id)
    course.presenceByUser[user_id] = time.time() * 1000


# Assemble ASGI app with Socket.IO mounted
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)

# ------------------- LTI Gateway (Complete Implementation) --------------------
from app.lti.routes import router as lti_router
from app.api.routes import router as api_router

fastapi_app.include_router(lti_router, prefix='/lti')
fastapi_app.include_router(api_router, prefix='/api')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=True)
