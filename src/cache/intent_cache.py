"""
Redis-backed intent analysis cache.

Caches the result of Gemini intent analysis (domain extraction, query refinement)
keyed by a SHA-256 hash of the normalised query. TTL defaults to 1 hour.

Falls back gracefully to a no-op if Redis is unavailable.
"""

import os
import json
import hashlib
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

INTENT_CACHE_TTL = int(os.getenv("INTENT_CACHE_TTL_SECONDS", "3600"))


class IntentCache:
    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._client = None
        self._available = False
        self._connect()

    def _connect(self):
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
            )
            self._available = True
            logger.info(f"Intent cache connected to Redis at {self.redis_url}")
        except Exception as e:
            logger.warning(f"Redis unavailable — intent cache disabled: {e}")
            self._available = False

    def _make_key(self, query: str) -> str:
        normalised = query.lower().strip()
        digest = hashlib.sha256(normalised.encode()).hexdigest()
        return f"intent:{digest}"

    async def get(self, query: str) -> Optional[Dict[str, Any]]:
        if not self._available or self._client is None:
            return None
        try:
            key = self._make_key(query)
            raw = await self._client.get(key)
            if raw:
                logger.debug(f"Intent cache HIT for query: {query[:50]}")
                return json.loads(raw)
            return None
        except Exception as e:
            logger.warning(f"Intent cache get failed: {e}")
            return None

    async def set(self, query: str, intent: Dict[str, Any]):
        if not self._available or self._client is None:
            return
        try:
            key = self._make_key(query)
            await self._client.setex(key, INTENT_CACHE_TTL, json.dumps(intent))
            logger.debug(f"Intent cache SET for query: {query[:50]}")
        except Exception as e:
            logger.warning(f"Intent cache set failed: {e}")

    async def invalidate(self, query: str):
        if not self._available or self._client is None:
            return
        try:
            key = self._make_key(query)
            await self._client.delete(key)
        except Exception as e:
            logger.warning(f"Intent cache invalidate failed: {e}")

    async def ping(self) -> bool:
        """Returns True if Redis is reachable."""
        if not self._available or self._client is None:
            return False
        try:
            await self._client.ping()
            return True
        except Exception:
            return False
