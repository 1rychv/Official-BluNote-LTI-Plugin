import os
import time
from typing import Dict, Any, List, Set
from urllib.parse import parse_qs
import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import socketio

from app.database.connection import init_redis, init_postgres, close_connections, get_postgres
from app.database.redis_models import RedisOnlyAppState


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


@fastapi_app.on_event("startup")
async def startup_event():
    """Initialize database connections and run migrations."""
    logger.info("Starting up BluNote LTI backend...")
    try:
        await init_redis()
        await init_postgres()

        # Run migrations only if PostgreSQL is available
        postgres_pool = get_postgres()
        if postgres_pool:
            try:
                from app.database.migrations import run_migrations
                await run_migrations(postgres_pool)
                logger.info("Database initialization completed successfully with PostgreSQL")
            except ImportError:
                logger.info("PostgreSQL migrations not available, continuing with Redis-only mode")

            # Start roster sync job if PostgreSQL is available
            try:
                from app.lti.services import roster_sync_job
                await roster_sync_job.start()
                logger.info("Started NRPS roster synchronization job")
            except Exception as e:
                logger.warning(f"Could not start roster sync job: {e}")
        else:
            logger.info("Database initialization completed successfully (Redis-only mode)")
    except Exception as e:
        logger.error(f"Failed to initialize databases: {e}")
        raise


@fastapi_app.on_event("shutdown")
async def shutdown_event():
    """Close database connections."""
    logger.info("Shutting down BluNote LTI backend...")

    # Stop roster sync job if running
    try:
        from app.lti.services import roster_sync_job
        await roster_sync_job.stop()
        logger.info("Stopped NRPS roster synchronization job")
    except Exception:
        pass

    await close_connections()
    logger.info("Shutdown completed")

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=[ALLOWED_ORIGIN])


# Initialize persistent state
state = RedisOnlyAppState()

# For debouncing - keep in memory as it's short-lived
last_press_by_user: Dict[str, float] = {}

# Logger setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_course_state(course_id: str):
    """Get persistent course state manager."""
    return state.get_course_state(course_id)


async def compute_metrics(course_id: str) -> Dict[str, Any]:
    """Compute metrics using persistent storage."""
    course_state = get_course_state(course_id)
    metrics = await course_state.compute_metrics()
    metrics["debounceSec"] = DEBOUNCE_MS // 1000
    metrics["threshold"] = THRESHOLD_PERCENT
    return metrics


async def generate_tutoring_stub(course_id: str) -> Dict[str, Any]:
    """Generate tutoring content with slide context (ready for Step 5 enhancement)"""
    # Get current slide context for better tutoring
    from app.services.slide_service import slide_service
    slide_context = await slide_service.get_slide_context(course_id)

    # Basic context-aware content
    if slide_context.slide_title:
        title = f"Help with {slide_context.slide_title}"
        text = f"It looks like several students are confused about {slide_context.slide_title}. Here's a quick explanation to help clarify this topic."
    else:
        title = "Quick Explanation"
        text = "It looks like several students are confused. Here is a concise recap to help with the current topic."

    return {
        "courseId": course_id,
        "title": title,
        "text": text,
        "slide_context": {
            "slide_number": slide_context.slide_number,
            "slide_title": slide_context.slide_title
        } if slide_context.slide_number else None,
        "practice": [
            {"q": "Can you explain this concept in your own words?", "a": "Free response"},
            {"q": "What's the main point of this section?", "a": "Think about the key takeaway"},
        ],
    }


async def maybe_trigger(course_id: str) -> None:
    """Check if confusion threshold is met and trigger tutoring."""
    course_state = get_course_state(course_id)
    now_ms = time.time() * 1000
    metrics = await compute_metrics(course_id)

    last_trigger = await course_state.get_last_trigger_time()
    if metrics['pct'] >= THRESHOLD_PERCENT and (now_ms - last_trigger) >= COOLDOWN_MS:
        await course_state.set_last_trigger_time(now_ms)
        confused_users = list(await course_state.get_unique_users_in_window(now_ms))

        # Record trigger event
        await course_state.record_trigger_event(confused_users, metrics['pct'], THRESHOLD_PERCENT)

        # Generate and persist tutoring content
        for user_id in confused_users:
            content = await generate_tutoring_stub(course_id)
            await course_state.persist_tutoring_content(user_id, content)

            # Notify connected sockets for this user
            user_sockets = state.get_user_sockets(user_id)
            for sid in user_sockets:
                await sio.emit('tutoring', content, to=sid)

        await sio.emit('trigger', {
            'courseId': course_id,
            'pct': metrics['pct'],
            'threshold': THRESHOLD_PERCENT,
            'at': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()),
        }, room=f'course:{course_id}')


async def record_press(course_id: str, user_id: str) -> bool:
    """Record a confusion press with debouncing and slide context."""
    now_ms = time.time() * 1000
    last = last_press_by_user.get(user_id, 0)

    # Check debounce
    if (now_ms - last) < DEBOUNCE_MS:
        return False

    # Update debounce tracker
    last_press_by_user[user_id] = now_ms

    # Get current slide context
    from app.services.slide_service import slide_service
    slide_context = await slide_service.get_slide_context(course_id)

    # Record in persistent storage with slide context
    course_state = get_course_state(course_id)
    await course_state.record_confused_event(user_id, now_ms, slide_context=slide_context)

    return True


@fastapi_app.get('/health')
async def health():
    """Health check endpoint with database connectivity status."""
    try:
        # Test Redis connectivity
        from app.database.connection import get_redis
        redis_client = get_redis()
        await redis_client.ping()
        redis_status = "ok"
    except Exception as e:
        redis_status = f"error: {str(e)}"

    try:
        # Test PostgreSQL connectivity
        postgres_pool = get_postgres()
        if postgres_pool:
            async with postgres_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            postgres_status = "ok"
        else:
            postgres_status = "not configured"
    except Exception as e:
        postgres_status = f"error: {str(e)}"

    return {
        "ok": redis_status == "ok" and (postgres_status == "ok" or postgres_status == "not configured"),
        "time": time.strftime('%Y-%m-%dT%H:%M:%S'),
        "redis": redis_status,
        "postgres": postgres_status
    }


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

        # Unregister socket from state
        state.unregister_socket(sid)

        # If this was a student's last socket, their presence will naturally expire
        # from Redis after the window period


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
            state.register_socket(sid, user_id)

            # Update presence for students
            if claims.get("is_student", False):
                course_state = get_course_state(claims["course_id"])
                await course_state.update_presence(user_id)

            # Send initial metrics
            metrics = {
                "courseId": claims["course_id"],
                **await compute_metrics(claims["course_id"])
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
    state.register_socket(sid, user_id)

    # Join course room
    await sio.enter_room(sid, f'course:{course_id}')

    # Update presence for students
    if role == 'student':
        course_state = get_course_state(course_id)
        await course_state.update_presence(user_id)

    # Send initial metrics
    metrics = {"courseId": course_id, **await compute_metrics(course_id)}
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
    accepted = await record_press(course_id, user_id)

    # Update presence when interacting
    course_state = get_course_state(course_id)
    await course_state.update_presence(user_id)

    # Broadcast updated metrics
    metrics = {"courseId": course_id, **await compute_metrics(course_id)}
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
    course_state = get_course_state(course_id)
    await course_state.update_presence(user_id)


@sio.on('update_slide')
async def on_update_slide(sid, data):
    """Handle slide update from instructor"""
    info = sid_map.get(sid)
    if not info:
        await sio.emit('error', {'message': 'Unauthorized'}, to=sid)
        return

    # Check if user is an instructor
    if not info.get('is_instructor', False):
        await sio.emit('error', {'message': 'Only instructors can update slides'}, to=sid)
        return

    course_id = info.get('course_id')
    user_id = info.get('user_id')

    # Validate data
    slide_number = data.get('slide_number', 1)
    slide_title = data.get('slide_title', 'Untitled Slide')

    try:
        # Store slide information
        from app.services.slide_service import slide_service
        slide_info = await slide_service.store_current_slide(
            course_id=course_id,
            slide_number=slide_number,
            slide_title=slide_title,
            updated_by=user_id
        )

        # Broadcast slide change to all course participants
        slide_data = {
            'slide_number': slide_info.slide_number,
            'slide_title': slide_info.slide_title,
            'updated_at': slide_info.updated_at.isoformat(),
            'updated_by': slide_info.updated_by
        }

        await sio.emit('slide_changed', slide_data, room=f'course:{course_id}')
        await sio.emit('slide_update_response', {
            'status': 'success',
            'slide': slide_data
        }, to=sid)

        logger.info(f"Slide updated in course {course_id}: Slide {slide_number} - {slide_title}")

    except Exception as e:
        logger.error(f"Failed to update slide: {e}")
        await sio.emit('slide_update_response', {
            'status': 'error',
            'message': str(e)
        }, to=sid)


# Assemble ASGI app with Socket.IO mounted
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)

# ------------------- LTI Gateway (Complete Implementation) --------------------
from app.lti.routes import router as lti_router
from app.api.routes import router as api_router
from app.api.services_routes import router as services_router
from app.api.slide_routes import router as slide_router

fastapi_app.include_router(lti_router, prefix='/lti')
fastapi_app.include_router(api_router, prefix='/api')
fastapi_app.include_router(services_router)
fastapi_app.include_router(slide_router)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=True)
