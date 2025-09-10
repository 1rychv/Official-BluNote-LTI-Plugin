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


@fastapi_app.get('/api/course/{course_id}/metrics')
async def get_metrics(course_id: str):
    data = {"courseId": course_id, "threshold": THRESHOLD_PERCENT, **compute_metrics(course_id)}
    return JSONResponse(content=data)


@fastapi_app.get('/api/course/{course_id}/roster')
async def get_roster(course_id: str):
    course = get_course(course_id)
    return {"courseId": course_id, "roster": int(course.roster or 0)}


@fastapi_app.post('/api/course/{course_id}/roster')
async def post_roster(course_id: str, payload: Dict[str, Any]):
    roster = payload.get('roster')
    try:
        value = int(roster)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid roster"})
    if value < 1:
        return JSONResponse(status_code=400, content={"error": "Invalid roster"})
    course = get_course(course_id)
    course.roster = value
    return {"ok": True, "courseId": course_id, "roster": value}


@fastapi_app.get('/api/user/{user_id}/tutoring')
async def get_tutoring(user_id: str):
    content = None
    for course in state.courses.values():
        if user_id in course.tutoringByUser:
            content = course.tutoringByUser[user_id]
            break
    return {"userId": user_id, "content": content}


@sio.event
async def connect(sid, environ):
    # Parse query string for role, courseId, userId, name
    query = parse_qs(environ.get('QUERY_STRING', ''))
    role = (query.get('role', [None])[0] or 'student')
    course_id = (query.get('courseId', [None])[0] or 'course-dev')
    user_id = (query.get('userId', [None])[0] or f'user-{sid}')
    name = (query.get('name', [None])[0] or user_id)

    # Track socket by user for tutoring pushes
    state.socketsByUser.setdefault(user_id, set()).add(sid)

    # Join course room
    await sio.enter_room(sid, f'course:{course_id}')

    # Initial metrics
    metrics = {"courseId": course_id, "threshold": THRESHOLD_PERCENT, **compute_metrics(course_id)}
    await sio.emit('metrics', metrics, to=sid)

    # Update presence for students on connect
    if role == 'student':
        course = get_course(course_id)
        course.presenceByUser[user_id] = time.time() * 1000


@sio.event
async def confused(sid):
    # Recover course and user from session via query parsing again
    # (python-socketio doesn't keep query; we map sid -> userId via socketsByUser reverse lookup if needed)
    # For simplicity, we can’t get params here; require client to send them? Keep simple: emit metrics to course room based on first room name
    # We’ll store sid->(course_id,user_id) in a small map on connect
    pass


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


# Override connect to record mapping now that handlers are defined
@sio.on('connect')
async def connect_and_store(sid, environ):
    query = parse_qs(environ.get('QUERY_STRING', ''))
    course_id = (query.get('courseId', [None])[0] or 'course-dev')
    user_id = (query.get('userId', [None])[0] or f'user-{sid}')
    role = (query.get('role', [None])[0] or 'student')
    sid_map[sid] = {"course_id": course_id, "user_id": user_id, "role": role}
    await connect(sid, environ)


@sio.on('confused')
async def on_confused(sid):
    info = sid_map.get(sid) or {}
    course_id = info.get('course_id', 'course-dev')
    user_id = info.get('user_id', f'user-{sid}')
    accepted = record_press(course_id, user_id)
    # Update presence when interacting
    course = get_course(course_id)
    course.presenceByUser[user_id] = time.time() * 1000
    metrics = {"courseId": course_id, "threshold": THRESHOLD_PERCENT, **compute_metrics(course_id)}
    await sio.emit('metrics', metrics, room=f'course:{course_id}')
    if accepted:
        await maybe_trigger(course_id)


@sio.on('presence')
async def on_presence(sid):
    info = sid_map.get(sid) or {}
    course_id = info.get('course_id', 'course-dev')
    user_id = info.get('user_id', f'user-{sid}')
    course = get_course(course_id)
    course.presenceByUser[user_id] = time.time() * 1000


# Assemble ASGI app with Socket.IO mounted
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=True)
