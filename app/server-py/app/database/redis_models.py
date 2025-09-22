"""Redis-only models for testing without PostgreSQL dependency."""
import time
import json
import os
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta
import redis.asyncio as redis
from .connection import get_redis
import logging

logger = logging.getLogger(__name__)

# Constants
WINDOW_MS = 1000 * int(os.getenv("WINDOW_SEC", "120"))
TUTORING_TTL = 3600  # 1 hour
MIN_AUTO_ROSTER = 3
DEFAULT_ROSTER = 20


class RedisOnlyCourseState:
    """Redis-only course state management for testing."""

    def __init__(self, course_id: str):
        self.course_id = course_id
        self._redis = None

    def _get_redis(self):
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    @property
    def redis(self):
        return self._get_redis()

    async def record_confused_event(self, user_id: str, ts: Optional[float] = None) -> bool:
        """Record a confusion event in Redis."""
        if ts is None:
            ts = time.time() * 1000

        # Redis for real-time tracking
        redis_key = f"presses:{self.course_id}"
        await self.redis.zadd(redis_key, {user_id: ts})
        await self.redis.expire(redis_key, WINDOW_MS // 1000 * 2)

        # Also log to Redis for persistence simulation
        log_key = f"events:{self.course_id}"
        event = {
            "user_id": user_id,
            "type": "confused",
            "timestamp": ts,
            "occurred_at": datetime.fromtimestamp(ts / 1000).isoformat()
        }
        await self.redis.lpush(log_key, json.dumps(event))
        await self.redis.expire(log_key, 24 * 3600)  # Keep for 24 hours

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

        # Fallback to manual override from Redis
        roster_key = f"roster:{self.course_id}"
        roster_override = await self.redis.get(roster_key)
        if roster_override:
            return int(roster_override)

        return DEFAULT_ROSTER

    async def update_roster_override(self, roster: int):
        """Update manual roster override in Redis."""
        roster_key = f"roster:{self.course_id}"
        await self.redis.set(roster_key, roster, ex=7 * 24 * 3600)  # Keep for 7 days

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
        """Persist tutoring content to Redis."""
        # Store in tutoring log
        tutoring_log_key = f"tutoring_log:{self.course_id}"
        log_entry = {
            "user_id": user_id,
            "content": content,
            "delivered_at": datetime.now().isoformat()
        }
        await self.redis.lpush(tutoring_log_key, json.dumps(log_entry))
        await self.redis.expire(tutoring_log_key, 24 * 3600)  # Keep for 24 hours

        # Cache for user
        cache_key = f"tutoring:{user_id}"
        await self.redis.set(cache_key, json.dumps(content), ex=TUTORING_TTL)

    async def get_tutoring_content(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get cached tutoring content for a user."""
        cache_key = f"tutoring:{user_id}"
        content = await self.redis.get(cache_key)
        return json.loads(content) if content else None

    async def record_trigger_event(self, confused_users: List[str], pct: int, threshold: int):
        """Record a trigger event in Redis."""
        trigger_log_key = f"triggers:{self.course_id}"
        trigger_event = {
            "users": confused_users,
            "confusion_pct": pct,
            "threshold": threshold,
            "confused_count": len(confused_users),
            "occurred_at": datetime.now().isoformat()
        }
        await self.redis.lpush(trigger_log_key, json.dumps(trigger_event))
        await self.redis.expire(trigger_log_key, 24 * 3600)  # Keep for 24 hours

    async def compute_metrics(self) -> Dict[str, Any]:
        """Compute real-time metrics using Redis."""
        now_ms = time.time() * 1000

        # Get unique confused users in window
        confused_users = await self.get_unique_users_in_window(now_ms)
        unique_count = len(confused_users)

        # Get roster size
        roster = await self.get_roster_size()

        # Get tutoring count from Redis log for the window
        tutoring_log_key = f"tutoring_log:{self.course_id}"
        tutoring_entries = await self.redis.lrange(tutoring_log_key, 0, -1)
        tutoring_count = 0
        window_start = datetime.fromtimestamp((now_ms - WINDOW_MS) / 1000)

        for entry_str in tutoring_entries:
            try:
                entry = json.loads(entry_str)
                delivered_at = datetime.fromisoformat(entry["delivered_at"])
                if delivered_at >= window_start:
                    tutoring_count += 1
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

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
            "tutoringCount": tutoring_count
        }


class RedisOnlyAppState:
    """Application-wide state management with Redis-only backing."""

    def __init__(self):
        self._redis = None
        self._socket_user_map: Dict[str, str] = {}  # sid -> user_id

    def _get_redis(self):
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    def get_course_state(self, course_id: str) -> RedisOnlyCourseState:
        """Get a course state manager."""
        return RedisOnlyCourseState(course_id)

    def register_socket(self, sid: str, user_id: str):
        """Register a socket connection for a user."""
        self._socket_user_map[sid] = user_id

    def unregister_socket(self, sid: str) -> Optional[str]:
        """Unregister a socket connection and return the user_id."""
        return self._socket_user_map.pop(sid, None)

    def get_user_sockets(self, user_id: str) -> List[str]:
        """Get all socket IDs for a user."""
        return [sid for sid, uid in self._socket_user_map.items() if uid == user_id]