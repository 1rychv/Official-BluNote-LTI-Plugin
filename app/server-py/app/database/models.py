"""Database models and operations for persistent state management."""
import time
import json
import uuid
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta
import redis.asyncio as redis
import asyncpg
from .connection import get_redis, get_postgres
import logging

logger = logging.getLogger(__name__)

# Constants from main.py
WINDOW_MS = 1000 * int(os.getenv("WINDOW_SEC", "120"))
TUTORING_TTL = 3600  # 1 hour
MIN_AUTO_ROSTER = 3
DEFAULT_ROSTER = 20

import os


class PersistentCourseState:
    """Redis/PostgreSQL-backed course state management."""

    def __init__(self, course_id: str):
        self.course_id = course_id
        self.redis = get_redis()
        self.postgres = get_postgres()

    async def record_confused_event(self, user_id: str, ts: Optional[float] = None) -> bool:
        """Record a confusion event in Redis and PostgreSQL."""
        if ts is None:
            ts = time.time() * 1000

        # Redis for real-time tracking
        redis_key = f"presses:{self.course_id}"
        await self.redis.zadd(redis_key, {user_id: ts})
        await self.redis.expire(redis_key, WINDOW_MS // 1000 * 2)

        # PostgreSQL for persistence
        async with self.postgres.acquire() as conn:
            await conn.execute("""
                INSERT INTO events (course_id, user_id, type, payload, occurred_at)
                VALUES ($1, $2, $3, $4, $5)
            """, self.course_id, user_id, "confused", {},
            datetime.fromtimestamp(ts / 1000))

        return True

    async def get_unique_users_in_window(self, now_ms: Optional[float] = None) -> Set[str]:
        """Get unique confused users in the current window."""
        if now_ms is None:
            now_ms = time.time() * 1000

        redis_key = f"presses:{self.course_id}"
        # Remove old entries
        await self.redis.zremrangebyscore(redis_key, "-inf", now_ms - WINDOW_MS)

        # Get current entries
        users = await self.redis.zrange(redis_key, 0, -1)
        return set(users) if users else set()

    async def update_presence(self, user_id: str, ts: Optional[float] = None):
        """Update user presence for auto-roster calculation."""
        if ts is None:
            ts = time.time() * 1000

        presence_key = f"presence:{self.course_id}"
        await self.redis.zadd(presence_key, {user_id: ts})
        await self.redis.expire(presence_key, WINDOW_MS // 1000 * 2)

    async def get_roster_size(self) -> int:
        """Get effective roster size (auto-detected or manual override)."""
        # Check auto-roster from presence
        presence_key = f"presence:{self.course_id}"
        now_ms = time.time() * 1000
        await self.redis.zremrangebyscore(presence_key, "-inf", now_ms - WINDOW_MS)
        auto_count = await self.redis.zcard(presence_key)

        if auto_count >= MIN_AUTO_ROSTER:
            return auto_count

        # Fallback to manual override from PostgreSQL
        async with self.postgres.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT roster_override FROM courses WHERE id = $1",
                self.course_id
            )
            if row and row['roster_override']:
                return row['roster_override']

        return DEFAULT_ROSTER

    async def update_roster_override(self, roster: int):
        """Update manual roster override in PostgreSQL."""
        async with self.postgres.acquire() as conn:
            await conn.execute("""
                INSERT INTO courses (id, roster_override, created_at)
                VALUES ($1, $2, now())
                ON CONFLICT (id)
                DO UPDATE SET roster_override = $2
            """, self.course_id, roster)

    async def get_last_trigger_time(self) -> float:
        """Get last trigger time from Redis."""
        trigger_key = f"last_trigger:{self.course_id}"
        last_trigger = await self.redis.get(trigger_key)
        return float(last_trigger) if last_trigger else 0.0

    async def set_last_trigger_time(self, ts: Optional[float] = None):
        """Set last trigger time in Redis."""
        if ts is None:
            ts = time.time() * 1000

        trigger_key = f"last_trigger:{self.course_id}"
        await self.redis.set(trigger_key, ts, ex=3600)  # Expire after 1 hour

    async def persist_tutoring_content(self, user_id: str, content: Dict[str, Any]):
        """Persist tutoring content to PostgreSQL and cache in Redis."""
        # Store in PostgreSQL
        async with self.postgres.acquire() as conn:
            await conn.execute("""
                INSERT INTO tutoring_sessions (course_id, user_id, content, delivered_at)
                VALUES ($1, $2, $3, now())
            """, self.course_id, user_id, json.dumps(content))

        # Cache in Redis with TTL
        cache_key = f"tutoring:{user_id}"
        await self.redis.set(cache_key, json.dumps(content), ex=TUTORING_TTL)

    async def get_tutoring_content(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get cached tutoring content for a user."""
        cache_key = f"tutoring:{user_id}"
        content = await self.redis.get(cache_key)
        return json.loads(content) if content else None

    async def record_trigger_event(self, confused_users: List[str], pct: int, threshold: int):
        """Record a trigger event in PostgreSQL."""
        async with self.postgres.acquire() as conn:
            await conn.execute("""
                INSERT INTO events (course_id, type, payload, occurred_at)
                VALUES ($1, $2, $3, now())
            """, self.course_id, "tutoring_trigger", json.dumps({
                "users": confused_users,
                "confusion_pct": pct,
                "threshold": threshold,
                "confused_count": len(confused_users)
            }))

    async def compute_metrics(self) -> Dict[str, Any]:
        """Compute real-time metrics using Redis and PostgreSQL."""
        now_ms = time.time() * 1000

        # Get unique confused users in window
        confused_users = await self.get_unique_users_in_window(now_ms)
        unique_count = len(confused_users)

        # Get roster size
        roster = await self.get_roster_size()

        # Get tutoring count from PostgreSQL for the window
        async with self.postgres.acquire() as conn:
            tutoring_count = await conn.fetchval("""
                SELECT COUNT(*) FROM tutoring_sessions
                WHERE course_id = $1
                AND delivered_at >= $2
            """, self.course_id, datetime.fromtimestamp((now_ms - WINDOW_MS) / 1000))

        # Determine roster source
        presence_key = f"presence:{self.course_id}"
        await self.redis.zremrangebyscore(presence_key, "-inf", now_ms - WINDOW_MS)
        auto_count = await self.redis.zcard(presence_key)
        roster_source = 'auto' if auto_count >= MIN_AUTO_ROSTER else 'manual'

        pct = round((unique_count / max(1, roster)) * 100)

        return {
            "uniqueCount": unique_count,
            "roster": roster,
            "pct": pct,
            "windowSec": WINDOW_MS // 1000,
            "rosterSource": roster_source,
            "tutoringCount": tutoring_count or 0
        }


class PersistentAppState:
    """Application-wide state management with Redis/PostgreSQL backing."""

    def __init__(self):
        self.redis = get_redis()
        self.postgres = get_postgres()
        self._socket_user_map: Dict[str, str] = {}  # sid -> user_id

    def get_course_state(self, course_id: str) -> PersistentCourseState:
        """Get a course state manager."""
        return PersistentCourseState(course_id)

    def register_socket(self, sid: str, user_id: str):
        """Register a socket connection for a user."""
        self._socket_user_map[sid] = user_id

    def unregister_socket(self, sid: str) -> Optional[str]:
        """Unregister a socket connection and return the user_id."""
        return self._socket_user_map.pop(sid, None)

    def get_user_sockets(self, user_id: str) -> List[str]:
        """Get all socket IDs for a user."""
        return [sid for sid, uid in self._socket_user_map.items() if uid == user_id]