import os
import time
from typing import Dict, Any, List, Set
from urllib.parse import parse_qs

from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import socketio
import httpx
from jose import jwt
from jose.utils import base64url_encode
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


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

# ------------------- LTI Gateway (minimal scaffolding) --------------------
lti = APIRouter()

@lti.get('/.well-known/jwks.json')
async def jwks():
    return {"keys": [{**DEV_JWK, "kid": LTI_KID or DEV_JWK.get('kid', 'dev-kid')}]} 

@lti.get('/config')
async def tool_config(request: Request):
    base = str(request.base_url).rstrip('/')
    return {
        "title": "BluNote LTI Tool",
        "scopes": [
            "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly",
            "https://purl.imsglobal.org/spec/lti-ags/scope/score",
            "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
            "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly"
        ],
        "extensions": [],
        "public_jwk": DEV_JWK,
        "oidc_initiation_url": f"{base}/lti/oidc_login",
        "launch_url": f"{base}/lti/launch",
        "jwks_url": f"{base}/lti/.well-known/jwks.json",
    }

@lti.get('/oidc_login')
async def oidc_login(request: Request):
    if not (PLATFORM_AUTH_LOGIN_URL and PLATFORM_CLIENT_ID):
        return JSONResponse(status_code=500, content={"error": "Platform not configured in env. Set PLATFORM_* vars."})
    qp = request.query_params
    login_hint = qp.get('login_hint')
    lti_message_hint = qp.get('lti_message_hint')
    target_link_uri = qp.get('target_link_uri') or TOOL_REDIRECT_URI
    state_val = os.urandom(16).hex()
    nonce_val = os.urandom(16).hex()
    auth_url = (
        f"{PLATFORM_AUTH_LOGIN_URL}?" 
        f"response_type=id_token&response_mode=form_post&prompt=none&scope=openid&"
        f"client_id={PLATFORM_CLIENT_ID}&redirect_uri={target_link_uri}&state={state_val}&nonce={nonce_val}&"
        f"login_hint={login_hint or ''}&lti_message_hint={lti_message_hint or ''}"
    )
    return JSONResponse(status_code=200, content={"redirect": auth_url, "note": "Redirect your browser to this URL."})

@lti.post('/launch')
async def lti_launch(id_token: str = Form(...), state: str = Form(None)):
    claims = None
    verified = False
    try:
        if PLATFORM_JWKS_URL and PLATFORM_ISSUER and PLATFORM_CLIENT_ID:
            async with httpx.AsyncClient(timeout=10) as client:
                jwks = (await client.get(PLATFORM_JWKS_URL)).json()
            claims = jwt.decode(id_token, jwks, algorithms=['RS256'], audience=PLATFORM_CLIENT_ID, issuer=PLATFORM_ISSUER)
            verified = True
        else:
            # DEV: accept token without verification to allow local UI wiring
            claims = jwt.get_unverified_claims(id_token)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": "Invalid id_token", "detail": str(e)})

    roles = claims.get('https://purl.imsglobal.org/spec/lti/claim/roles', []) or []
    context = claims.get('https://purl.imsglobal.org/spec/lti/claim/context', {}) or {}
    res_link = claims.get('https://purl.imsglobal.org/spec/lti/claim/resource_link', {}) or {}
    name = claims.get('name') or (claims.get('given_name','') + ' ' + claims.get('family_name','')).strip() or 'User'
    sub = claims.get('sub') or claims.get('email') or 'user'
    course_id = context.get('id') or res_link.get('id') or 'COURSE1'
    is_instructor = any('Instructor' in r or 'Teacher' in r for r in roles)

    # Dev redirect to frontend
    if is_instructor:
        url = f"{FRONTEND_BASE}/instructor?courseId={course_id}&name={name}"
    else:
        url = f"{FRONTEND_BASE}/student?courseId={course_id}&userId={sub}&name={name}"

    return JSONResponse(content={
        "ok": True,
        "verified": verified,
        "redirect": url,
        "courseId": course_id,
        "role": "instructor" if is_instructor else "student",
    })

@lti.get('/dev/launch')
async def dev_launch(role: str = 'student', courseId: str = 'COURSE1', userId: str = 'dev1', name: str = 'Dev User'):
    if role.lower().startswith('inst'):
        url = f"{FRONTEND_BASE}/instructor?courseId={courseId}&name={name}"
    else:
        url = f"{FRONTEND_BASE}/student?courseId={courseId}&userId={userId}&name={name}"
    return {"redirect": url}

fastapi_app.include_router(lti, prefix='/lti')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=True)
# LTI platform/tool config
PLATFORM_ISSUER = os.getenv("PLATFORM_ISSUER")
PLATFORM_CLIENT_ID = os.getenv("PLATFORM_CLIENT_ID")
PLATFORM_AUTH_LOGIN_URL = os.getenv("PLATFORM_AUTH_LOGIN_URL")
PLATFORM_JWKS_URL = os.getenv("PLATFORM_JWKS_URL")
PLATFORM_DEPLOYMENT_ID = os.getenv("PLATFORM_DEPLOYMENT_ID")
TOOL_REDIRECT_URI = os.getenv("TOOL_REDIRECT_URI", f"http://localhost:{PORT}/lti/launch")

# Tool key pair (dev: generate ephemeral if env empty)
LTI_PRIVATE_KEY_PEM = os.getenv("LTI_PRIVATE_KEY_PEM")
LTI_KID = os.getenv("LTI_KID")

def _generate_rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode('utf-8')
    pub = key.public_key()
    pub_numbers = pub.public_numbers()
    e = pub_numbers.e.to_bytes((pub_numbers.e.bit_length() + 7) // 8, 'big')
    n = pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, 'big')
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "dev-kid",
        "n": base64url_encode(n).decode('utf-8'),
        "e": base64url_encode(e).decode('utf-8'),
    }
    return pem, jwk

if not LTI_PRIVATE_KEY_PEM:
    LTI_PRIVATE_KEY_PEM, DEV_JWK = _generate_rsa_key()
    LTI_KID = LTI_KID or DEV_JWK["kid"]
else:
    # derive public JWK from provided private key
    _key = serialization.load_pem_private_key(LTI_PRIVATE_KEY_PEM.encode('utf-8'), password=None)
    pub = _key.public_key()
    pub_numbers = pub.public_numbers()
    e = pub_numbers.e.to_bytes((pub_numbers.e.bit_length() + 7) // 8, 'big')
    n = pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, 'big')
    DEV_JWK = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": LTI_KID or "tool-kid",
        "n": base64url_encode(n).decode('utf-8'),
        "e": base64url_encode(e).decode('utf-8'),
    }
