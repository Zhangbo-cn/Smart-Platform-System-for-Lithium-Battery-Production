"""Session 事件总线：memory（单进程）| redis（多 Router SSE 共享）。"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, AsyncIterator, Literal, Protocol

import structlog

from platform_contracts.task_events import TaskEvent

logger = structlog.get_logger(__name__)

_HISTORY_LIMIT = 128
_CLOSE_SENTINEL = "__sse_close__"


class EventBus(Protocol):
    async def next_seq(self, session_id: str) -> int: ...
    async def history_since(self, session_id: str, after_seq: int = 0) -> list[TaskEvent]: ...
    async def publish(self, session_id: str, event: TaskEvent) -> None: ...
    async def close_session(self, session_id: str) -> None: ...
    def subscribe(self, session_id: str) -> asyncio.Queue[TaskEvent | None]: ...


class MemorySessionEventBus:
    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[TaskEvent | None]]] = defaultdict(list)
        self._seq: dict[str, int] = defaultdict(int)
        self._history: dict[str, list[TaskEvent]] = defaultdict(list)

    async def next_seq(self, session_id: str) -> int:
        self._seq[session_id] += 1
        return self._seq[session_id]

    async def history_since(self, session_id: str, after_seq: int = 0) -> list[TaskEvent]:
        return [e for e in self._history.get(session_id, []) if e.seq > after_seq]

    def subscribe(self, session_id: str) -> asyncio.Queue[TaskEvent | None]:
        q: asyncio.Queue[TaskEvent | None] = asyncio.Queue(maxsize=256)
        self._queues[session_id].append(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[TaskEvent | None]) -> None:
        subs = self._queues.get(session_id, [])
        if q in subs:
            subs.remove(q)

    async def publish(self, session_id: str, event: TaskEvent) -> None:
        hist = self._history[session_id]
        hist.append(event)
        if len(hist) > _HISTORY_LIMIT:
            del hist[: len(hist) - _HISTORY_LIMIT]
        for q in list(self._queues.get(session_id, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("event_bus.queue_full", session_id=session_id, seq=event.seq)

    async def close_session(self, session_id: str) -> None:
        for q in list(self._queues.get(session_id, [])):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass


class RedisSessionEventBus:
    """Redis List 存历史 + Pub/Sub 跨 Router 实例推送 SSE。"""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: Any = None

    async def _redis(self):
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._redis_url, decode_responses=True)
        return self._client

    @staticmethod
    def _log_key(session_id: str) -> str:
        return f"platform:events:{session_id}:log"

    @staticmethod
    def _seq_key(session_id: str) -> str:
        return f"platform:events:{session_id}:seq"

    @staticmethod
    def _pub_channel(session_id: str) -> str:
        return f"platform:events:{session_id}:pub"

    async def next_seq(self, session_id: str) -> int:
        r = await self._redis()
        return int(await r.incr(self._seq_key(session_id)))

    async def history_since(self, session_id: str, after_seq: int = 0) -> list[TaskEvent]:
        r = await self._redis()
        raw = await r.lrange(self._log_key(session_id), 0, -1)
        out: list[TaskEvent] = []
        for item in raw:
            try:
                ev = TaskEvent.model_validate_json(item)
            except Exception:
                continue
            if ev.seq > after_seq:
                out.append(ev)
        return out

    def subscribe(self, session_id: str) -> asyncio.Queue[TaskEvent | None]:
        raise NotImplementedError("Redis bus uses listen_live(); subscribe is memory-only")

    async def publish(self, session_id: str, event: TaskEvent) -> None:
        r = await self._redis()
        payload = event.model_dump_json()
        pipe = r.pipeline()
        pipe.rpush(self._log_key(session_id), payload)
        pipe.ltrim(self._log_key(session_id), -_HISTORY_LIMIT, -1)
        pipe.publish(self._pub_channel(session_id), payload)
        await pipe.execute()

    async def close_session(self, session_id: str) -> None:
        r = await self._redis()
        await r.publish(self._pub_channel(session_id), json.dumps({"_close": _CLOSE_SENTINEL}))

    async def listen_live(self, session_id: str) -> AsyncIterator[TaskEvent | None]:
        r = await self._redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(self._pub_channel(session_id))
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if not data:
                    continue
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if parsed.get("_close") == _CLOSE_SENTINEL:
                    yield None
                    return
                yield TaskEvent.model_validate(parsed)
        finally:
            await pubsub.unsubscribe(self._pub_channel(session_id))
            await pubsub.aclose()


_default_bus: EventBus = MemorySessionEventBus()
_bus: EventBus | None = None


def init_event_bus(backend: Literal["memory", "redis"], *, redis_url: str = "redis://localhost:6379/0") -> EventBus:
    global _bus
    if backend == "redis":
        _bus = RedisSessionEventBus(redis_url)
        logger.info("event_bus.backend", backend="redis", url=redis_url)
    else:
        _bus = MemorySessionEventBus()
        logger.info("event_bus.backend", backend="memory")
    return _bus


def get_event_bus() -> EventBus:
    return _bus if _bus is not None else _default_bus


# 兼容旧 import
SessionEventBus = MemorySessionEventBus
