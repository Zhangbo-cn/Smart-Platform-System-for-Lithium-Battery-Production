from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from config import get_settings


class ShortTermMemory:
    """Per-session conversation context, Agent intermediate state."""

    def __init__(self, redis: Redis | None = None, agent_name: str = "default") -> None:
        settings = get_settings()
        self.redis = redis or Redis.from_url(settings.redis_url, decode_responses=True)
        self.ttl = settings.short_term_ttl_seconds
        self.agent_name = agent_name

    def _key(self, session_id: str, slot: str) -> str:
        return f"agent:stm:{self.agent_name}:{session_id}:{slot}"

    async def get(self, session_id: str, slot: str = "state") -> Any | None:
        raw = await self.redis.get(self._key(session_id, slot))
        return json.loads(raw) if raw else None

    async def set(self, session_id: str, value: Any, slot: str = "state") -> None:
        await self.redis.set(
            self._key(session_id, slot),
            json.dumps(value, ensure_ascii=False, default=str),
            ex=self.ttl,
        )

    async def append_turn(self, session_id: str, role: str, content: str) -> None:
        key = self._key(session_id, "turns")
        await self.redis.rpush(key, json.dumps({"role": role, "content": content}))
        await self.redis.expire(key, self.ttl)

    async def get_turns(self, session_id: str, last_n: int = 10) -> list[dict]:
        key = self._key(session_id, "turns")
        raw = await self.redis.lrange(key, -last_n, -1)
        return [json.loads(r) for r in raw]

    async def reset_turns(self, session_id: str) -> None:
        key = self._key(session_id, "turns")
        await self.redis.delete(key)

    async def clear(self, session_id: str) -> None:
        async for k in self.redis.scan_iter(match=f"agent:stm:{session_id}:*"):
            await self.redis.delete(k)
