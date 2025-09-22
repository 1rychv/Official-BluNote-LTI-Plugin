import json
import time
from typing import Dict, Any, Optional


class MemoryCache:
    """
    Simple in-memory cache for development.
    In production, this should be replaced with Redis.
    """

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}

    async def get(self, key: str) -> Optional[str]:
        if key in self._data:
            entry = self._data[key]
            if entry["expires_at"] > time.time():
                return entry["value"]
            else:
                del self._data[key]
        return None

    async def set(self, key: str, value: str, ttl: int = 300) -> None:
        self._data[key] = {
            "value": value,
            "expires_at": time.time() + ttl
        }

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def expire(self, key: str, ttl: int) -> None:
        if key in self._data:
            self._data[key]["expires_at"] = time.time() + ttl

    def cleanup_expired(self) -> None:
        """Clean up expired entries"""
        now = time.time()
        expired_keys = [
            key for key, entry in self._data.items()
            if entry["expires_at"] <= now
        ]
        for key in expired_keys:
            del self._data[key]


token_cache = MemoryCache()