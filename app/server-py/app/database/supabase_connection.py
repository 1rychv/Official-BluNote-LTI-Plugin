"""Supabase connection management for BluNote LTI."""
import os
import json
import time
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta
import logging
from supabase import create_client, Client
from postgrest import APIError
from storage3.utils import StorageException

logger = logging.getLogger(__name__)

# Global Supabase client
supabase_client: Optional[Client] = None

# Constants
WINDOW_MS = 1000 * int(os.getenv("WINDOW_SEC", "120"))
TUTORING_TTL = 3600  # 1 hour
MIN_AUTO_ROSTER = 3
DEFAULT_ROSTER = 20


async def init_supabase():
    """Initialize Supabase client."""
    global supabase_client

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        logger.warning("SUPABASE_URL or SUPABASE_ANON_KEY not provided. Falling back to Redis-only mode.")
        return

    try:
        supabase_client = create_client(supabase_url, supabase_key)
        logger.info("Supabase client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        raise


def get_supabase() -> Client:
    """Get Supabase client."""
    if not supabase_client:
        raise RuntimeError("Supabase client not initialized")
    return supabase_client


class SupabaseCourseState:
    """Supabase-powered course state management."""

    def __init__(self, course_id: str):
        self.course_id = course_id

    @property
    def supabase(self) -> Client:
        return get_supabase()

    async def record_confused_event(self, user_id: str, ts: Optional[float] = None, slide_context=None) -> bool:
        """Record a confusion event using Redis wrapper + PostgreSQL persistence."""
        if ts is None:
            ts = time.time() * 1000

        try:
            # Use Redis wrapper for real-time tracking (fast)
            redis_key = f"presses:{self.course_id}"
            try:
                # Store in Redis wrapper using SQL interface
                self.supabase.rpc('redis_zadd', {
                    'key': redis_key,
                    'score': ts,
                    'member': user_id
                }).execute()

                # Set expiry for cleanup
                self.supabase.rpc('redis_expire', {
                    'key': redis_key,
                    'seconds': (WINDOW_MS // 1000) * 2
                }).execute()
            except Exception as redis_error:
                logger.warning(f"Redis wrapper unavailable, falling back to PostgreSQL: {redis_error}")

            # Store in PostgreSQL for persistence (slower but reliable)
            event_data = {
                "course_id": self.course_id,
                "user_id": user_id,
                "event_type": "confused",
                "timestamp": ts,
                "occurred_at": datetime.fromtimestamp(ts / 1000).isoformat(),
                "window_end": datetime.fromtimestamp((ts + WINDOW_MS) / 1000).isoformat()
            }

            # Add slide context if available
            if slide_context:
                event_data["slide_context"] = {
                    "slide_number": slide_context.slide_number,
                    "slide_title": slide_context.slide_title,
                    "slide_timestamp": slide_context.timestamp.isoformat() if slide_context.timestamp else None
                }

            # Insert into confusion_events table
            self.supabase.table("confusion_events").insert(event_data).execute()

            # Also store in cache table for Redis fallback
            cache_data = {
                "course_id": self.course_id,
                "user_id": user_id,
                "timestamp": ts,
                "expires_at": datetime.fromtimestamp((ts + WINDOW_MS) / 1000).isoformat()
            }
            self.supabase.table("confusion_cache").upsert(cache_data, on_conflict="course_id,user_id").execute()

            return True
        except Exception as e:
            logger.error(f"Failed to record confused event: {e}")
            return False

    async def get_unique_users_in_window(self, now_ms: Optional[float] = None) -> Set[str]:
        """Get unique confused users in the current window."""
        if now_ms is None:
            now_ms = time.time() * 1000

        try:
            window_start = datetime.fromtimestamp((now_ms - WINDOW_MS) / 1000).isoformat()

            # Query confusion_cache for active entries
            result = self.supabase.table("confusion_cache")\
                .select("user_id")\
                .eq("course_id", self.course_id)\
                .gte("expires_at", datetime.now().isoformat())\
                .execute()

            return set(item["user_id"] for item in result.data)
        except Exception as e:
            logger.error(f"Failed to get unique users in window: {e}")
            return set()

    async def update_presence(self, user_id: str, ts: Optional[float] = None):
        """Update user presence for auto-roster calculation."""
        if ts is None:
            ts = time.time() * 1000

        try:
            presence_data = {
                "course_id": self.course_id,
                "user_id": user_id,
                "timestamp": ts,
                "expires_at": datetime.fromtimestamp((ts + WINDOW_MS) / 1000).isoformat()
            }

            self.supabase.table("presence_tracking")\
                .upsert(presence_data, on_conflict="course_id,user_id")\
                .execute()
        except Exception as e:
            logger.error(f"Failed to update presence: {e}")

    async def get_roster_size(self) -> int:
        """Get effective roster size (auto-detected or manual override)."""
        try:
            # Check auto-roster from presence
            result = self.supabase.table("presence_tracking")\
                .select("user_id", count="exact")\
                .eq("course_id", self.course_id)\
                .gte("expires_at", datetime.now().isoformat())\
                .execute()

            auto_count = result.count or 0

            if auto_count >= MIN_AUTO_ROSTER:
                return auto_count

            # Fallback to manual override
            roster_result = self.supabase.table("course_rosters")\
                .select("roster_size")\
                .eq("course_id", self.course_id)\
                .execute()

            if roster_result.data:
                return roster_result.data[0]["roster_size"]

            return DEFAULT_ROSTER
        except Exception as e:
            logger.error(f"Failed to get roster size: {e}")
            return DEFAULT_ROSTER

    async def update_roster_override(self, roster: int):
        """Update manual roster override."""
        try:
            roster_data = {
                "course_id": self.course_id,
                "roster_size": roster,
                "updated_at": datetime.now().isoformat()
            }

            self.supabase.table("course_rosters")\
                .upsert(roster_data, on_conflict="course_id")\
                .execute()
        except Exception as e:
            logger.error(f"Failed to update roster override: {e}")

    async def get_last_trigger_time(self) -> float:
        """Get last trigger time."""
        try:
            result = self.supabase.table("trigger_cooldowns")\
                .select("last_trigger_time")\
                .eq("course_id", self.course_id)\
                .execute()

            if result.data:
                return float(result.data[0]["last_trigger_time"])
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get last trigger time: {e}")
            return 0.0

    async def set_last_trigger_time(self, ts: Optional[float] = None):
        """Set last trigger time."""
        if ts is None:
            ts = time.time() * 1000

        try:
            trigger_data = {
                "course_id": self.course_id,
                "last_trigger_time": ts,
                "updated_at": datetime.now().isoformat()
            }

            self.supabase.table("trigger_cooldowns")\
                .upsert(trigger_data, on_conflict="course_id")\
                .execute()
        except Exception as e:
            logger.error(f"Failed to set last trigger time: {e}")

    async def persist_tutoring_content(self, user_id: str, content: Dict[str, Any]):
        """Persist tutoring content with Supabase Storage backup."""
        try:
            timestamp = datetime.now()

            # Store in tutoring log (database)
            log_data = {
                "course_id": self.course_id,
                "user_id": user_id,
                "content": content,
                "delivered_at": timestamp.isoformat(),
                "expires_at": (timestamp + timedelta(seconds=TUTORING_TTL)).isoformat()
            }

            result = self.supabase.table("tutoring_logs").insert(log_data).execute()

            # Cache for user (database)
            cache_data = {
                "user_id": user_id,
                "content": content,
                "expires_at": (timestamp + timedelta(seconds=TUTORING_TTL)).isoformat()
            }

            self.supabase.table("tutoring_cache")\
                .upsert(cache_data, on_conflict="user_id")\
                .execute()

            # Also store in Supabase Storage for backup/analytics
            try:
                storage_path = f"tutoring/{self.course_id}/{user_id}/content-{int(timestamp.timestamp())}.json"
                storage_content = {
                    "content": content,
                    "delivered_at": timestamp.isoformat(),
                    "course_id": self.course_id,
                    "user_id": user_id
                }

                self.supabase.storage.from_("blunote-files")\
                    .upload(storage_path, json.dumps(storage_content, indent=2).encode())

                logger.info(f"Tutoring content backed up to storage: {storage_path}")
            except StorageException as se:
                logger.warning(f"Storage backup failed (non-critical): {se}")
            except Exception as storage_error:
                logger.warning(f"Storage backup failed (non-critical): {storage_error}")

        except Exception as e:
            logger.error(f"Failed to persist tutoring content: {e}")

    async def get_tutoring_content(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get cached tutoring content for a user."""
        try:
            result = self.supabase.table("tutoring_cache")\
                .select("content")\
                .eq("user_id", user_id)\
                .gte("expires_at", datetime.now().isoformat())\
                .execute()

            if result.data:
                return result.data[0]["content"]
            return None
        except Exception as e:
            logger.error(f"Failed to get tutoring content: {e}")
            return None

    async def record_trigger_event(self, confused_users: List[str], pct: int, threshold: int):
        """Record a trigger event."""
        try:
            trigger_data = {
                "course_id": self.course_id,
                "confused_users": confused_users,
                "confusion_pct": pct,
                "threshold": threshold,
                "confused_count": len(confused_users),
                "occurred_at": datetime.now().isoformat()
            }

            self.supabase.table("trigger_events").insert(trigger_data).execute()
        except Exception as e:
            logger.error(f"Failed to record trigger event: {e}")

    async def compute_metrics(self) -> Dict[str, Any]:
        """Compute real-time metrics."""
        now_ms = time.time() * 1000

        # Get unique confused users in window
        confused_users = await self.get_unique_users_in_window(now_ms)
        unique_count = len(confused_users)

        # Get roster size
        roster = await self.get_roster_size()

        # Get tutoring count from recent deliveries
        try:
            window_start = datetime.fromtimestamp((now_ms - WINDOW_MS) / 1000).isoformat()
            result = self.supabase.table("tutoring_logs")\
                .select("*", count="exact")\
                .eq("course_id", self.course_id)\
                .gte("delivered_at", window_start)\
                .execute()

            tutoring_count = result.count or 0
        except Exception:
            tutoring_count = 0

        # Determine roster source
        try:
            presence_result = self.supabase.table("presence_tracking")\
                .select("user_id", count="exact")\
                .eq("course_id", self.course_id)\
                .gte("expires_at", datetime.now().isoformat())\
                .execute()

            auto_count = presence_result.count or 0
            roster_source = 'auto' if auto_count >= MIN_AUTO_ROSTER else 'manual'
        except Exception:
            roster_source = 'manual'

        pct = round((unique_count / max(1, roster)) * 100)

        return {
            "uniqueCount": unique_count,
            "roster": roster,
            "pct": pct,
            "windowSec": WINDOW_MS // 1000,
            "rosterSource": roster_source,
            "tutoringCount": tutoring_count
        }


class SupabaseAppState:
    """Application-wide state management with Supabase backing."""

    def __init__(self):
        self._socket_user_map: Dict[str, str] = {}  # sid -> user_id

    def get_course_state(self, course_id: str) -> SupabaseCourseState:
        """Get a course state manager."""
        return SupabaseCourseState(course_id)

    def register_socket(self, sid: str, user_id: str):
        """Register a socket connection for a user."""
        self._socket_user_map[sid] = user_id

    def unregister_socket(self, sid: str) -> Optional[str]:
        """Unregister a socket connection and return the user_id."""
        return self._socket_user_map.pop(sid, None)

    def get_user_sockets(self, user_id: str) -> List[str]:
        """Get all socket IDs for a user."""
        return [sid for sid, uid in self._socket_user_map.items() if uid == user_id]


class SupabaseStorageHelper:
    """Helper class for Supabase Storage operations in BluNote."""

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client
        self.bucket_name = "blunote-files"

    async def store_slide_attachment(self, course_id: str, slide_number: int, file_content: bytes, filename: str) -> str:
        """Store slide attachment and return storage path."""
        try:
            storage_path = f"slides/{course_id}/slide-{slide_number}/{filename}"

            result = self.supabase.storage.from_(self.bucket_name)\
                .upload(storage_path, file_content)

            logger.info(f"Slide attachment stored: {storage_path}")
            return storage_path
        except Exception as e:
            logger.error(f"Failed to store slide attachment: {e}")
            raise

    async def get_slide_attachment_url(self, storage_path: str, expires_in: int = 3600) -> str:
        """Get signed URL for slide attachment."""
        try:
            result = self.supabase.storage.from_(self.bucket_name)\
                .create_signed_url(storage_path, expires_in)

            return result.get("signedURL", "")
        except Exception as e:
            logger.error(f"Failed to get slide attachment URL: {e}")
            return ""

    async def export_confusion_analytics(self, course_id: str, start_date: datetime, end_date: datetime) -> str:
        """Export confusion analytics to CSV and store in Supabase Storage."""
        try:
            # Query confusion events
            events_result = self.supabase.table("confusion_events")\
                .select("*")\
                .eq("course_id", course_id)\
                .gte("occurred_at", start_date.isoformat())\
                .lte("occurred_at", end_date.isoformat())\
                .order("occurred_at")\
                .execute()

            # Convert to CSV
            import csv
            from io import StringIO

            csv_buffer = StringIO()
            if events_result.data:
                writer = csv.DictWriter(csv_buffer, fieldnames=events_result.data[0].keys())
                writer.writeheader()
                writer.writerows(events_result.data)

            csv_content = csv_buffer.getvalue().encode()

            # Store in Supabase Storage
            date_str = start_date.strftime("%Y-%m-%d")
            storage_path = f"analytics/{course_id}/confusion-export-{date_str}.csv"

            self.supabase.storage.from_(self.bucket_name)\
                .upload(storage_path, csv_content)

            logger.info(f"Analytics exported: {storage_path}")
            return storage_path

        except Exception as e:
            logger.error(f"Failed to export analytics: {e}")
            raise

    async def store_lti_config(self, platform: str, config_data: Dict[str, Any]) -> str:
        """Store LTI platform configuration."""
        try:
            storage_path = f"lti/configs/{platform}/tool-config.json"
            config_content = json.dumps(config_data, indent=2).encode()

            self.supabase.storage.from_(self.bucket_name)\
                .upload(storage_path, config_content)

            logger.info(f"LTI config stored: {storage_path}")
            return storage_path
        except Exception as e:
            logger.error(f"Failed to store LTI config: {e}")
            raise

    async def list_tutoring_files(self, course_id: str, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List tutoring content files for a course or user."""
        try:
            folder_path = f"tutoring/{course_id}/"
            if user_id:
                folder_path += f"{user_id}/"

            result = self.supabase.storage.from_(self.bucket_name)\
                .list(folder_path)

            return result or []
        except Exception as e:
            logger.error(f"Failed to list tutoring files: {e}")
            return []

    async def cleanup_expired_files(self, days_old: int = 30):
        """Clean up old files from storage."""
        try:
            # This would typically be run as a scheduled job
            cutoff_date = datetime.now() - timedelta(days=days_old)

            # List all files and check dates
            # Implementation depends on your cleanup policy
            logger.info(f"Cleanup job would remove files older than {cutoff_date}")

        except Exception as e:
            logger.error(f"Failed to cleanup files: {e}")

    async def ensure_bucket_exists(self):
        """Ensure the BluNote storage bucket exists."""
        try:
            # List buckets to check if ours exists
            buckets = self.supabase.storage.list_buckets()

            bucket_exists = any(bucket.name == self.bucket_name for bucket in buckets)

            if not bucket_exists:
                # Create bucket
                self.supabase.storage.create_bucket(
                    self.bucket_name,
                    options={"public": False}  # Private bucket for BluNote
                )
                logger.info(f"Created storage bucket: {self.bucket_name}")
            else:
                logger.info(f"Storage bucket exists: {self.bucket_name}")

        except Exception as e:
            logger.warning(f"Could not ensure bucket exists: {e}")


def get_storage_helper() -> SupabaseStorageHelper:
    """Get Supabase Storage helper instance."""
    return SupabaseStorageHelper(get_supabase())