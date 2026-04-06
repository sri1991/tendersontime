"""
Redis-backed search result cache.

Caches the full ranked result list for a given (query, limit, corrigendum)
combination. TTL defaults to 10 minutes — safe for daily-updated tender data.

Falls back gracefully to a no-op if Redis is unavailable.
"""

import hashlib
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SEARCH_CACHE_TTL = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "600"))  # 10 min


class SearchCache:
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
            logger.info("Search cache connected to Redis.")
        except Exception as e:
            logger.warning(f"Redis unavailable — search cache disabled: {e}")
            self._available = False

    def _make_key(self, query: str, limit: int, include_corrigendum: bool) -> str:
        normalised = f"{query.lower().strip()}:{limit}:{include_corrigendum}"
        digest = hashlib.sha256(normalised.encode()).hexdigest()
        return f"search:{digest}"

    async def get(self, query: str, limit: int, include_corrigendum: bool) -> Optional[Dict[str, Any]]:
        if not self._available or self._client is None:
            return None
        try:
            key = self._make_key(query, limit, include_corrigendum)
            raw = await self._client.get(key)
            if raw:
                logger.debug(f"Search cache HIT: {query[:50]}")
                return json.loads(raw)
            return None
        except Exception as e:
            logger.warning(f"Search cache get failed: {e}")
            return None

    async def set(self, query: str, limit: int, include_corrigendum: bool, result: Dict[str, Any]):
        if not self._available or self._client is None:
            return
        try:
            key = self._make_key(query, limit, include_corrigendum)
            await self._client.setex(key, SEARCH_CACHE_TTL, json.dumps(result))
            logger.debug(f"Search cache SET: {query[:50]}")
        except Exception as e:
            logger.warning(f"Search cache set failed: {e}")

    async def invalidate_all(self):
        """Clears all search result cache entries (call after bulk ingestion)."""
        if not self._available or self._client is None:
            return
        try:
            keys = await self._client.keys("search:*")
            if keys:
                await self._client.delete(*keys)
                logger.info(f"Search cache cleared: {len(keys)} entries removed.")
        except Exception as e:
            logger.warning(f"Search cache invalidate_all failed: {e}")
