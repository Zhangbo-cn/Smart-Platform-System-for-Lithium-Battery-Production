"""SSE 流生成与任务事件发布。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator

from harness_core.events.bus import RedisSessionEventBus, get_event_bus
from harness_core.events.sse import format_sse
from platform_contracts.task_events import TERMINAL_TASK_EVENTS, TaskEvent, TaskEventType
from platform_contracts.task_state import TaskState


async def publish_task_event(
    bus,
    *,
    session_id: str,
    trace_id: str,
    event_type: TaskEventType,
    task_status: TaskState | None = None,
    step: str | None = None,
    agent: str | None = None,
    message: str | None = None,
    payload: dict | None = None,
) -> TaskEvent:
    seq = await bus.next_seq(session_id)
    event = TaskEvent(
        seq=seq,
        event=event_type,
        session_id=session_id,
        trace_id=trace_id,
        task_status=task_status,
        step=step,
        agent=agent,
        message=message,
        payload=payload,
    )
    await bus.publish(session_id, event)
    return event


async def sse_event_stream(
    session_id: str,
    *,
    after_seq: int = 0,
    heartbeat_interval: float = 15.0,
    bus=None,
) -> AsyncIterator[str]:
    bus = bus or get_event_bus()
    yield format_sse(
        TaskEventType.CONNECTED.value,
        {"session_id": session_id, "after_seq": after_seq, "ts": datetime.now(timezone.utc).isoformat()},
    )
    for past in await bus.history_since(session_id, after_seq):
        yield format_sse(past.sse_event_name(), past.sse_data(), event_id=past.seq)
        if past.event in TERMINAL_TASK_EVENTS:
            return

    if isinstance(bus, RedisSessionEventBus):
        closed = False
        live = bus.listen_live(session_id)

        async def _next_live():
            return await live.__anext__()

        try:
            while not closed:
                try:
                    item = await asyncio.wait_for(_next_live(), timeout=heartbeat_interval)
                except asyncio.TimeoutError:
                    yield format_sse(
                        TaskEventType.HEARTBEAT.value,
                        {"session_id": session_id, "ts": datetime.now(timezone.utc).isoformat()},
                    )
                    continue
                except StopAsyncIteration:
                    break
                if item is None:
                    break
                yield format_sse(item.sse_event_name(), item.sse_data(), event_id=item.seq)
                if item.event in TERMINAL_TASK_EVENTS:
                    closed = True
        finally:
            await live.aclose()
        return

    q = bus.subscribe(session_id)
    closed = False
    try:
        while not closed:
            try:
                item = await asyncio.wait_for(q.get(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                yield format_sse(
                    TaskEventType.HEARTBEAT.value,
                    {"session_id": session_id, "ts": datetime.now(timezone.utc).isoformat()},
                )
                continue
            if item is None:
                break
            yield format_sse(item.sse_event_name(), item.sse_data(), event_id=item.seq)
            if item.event in TERMINAL_TASK_EVENTS:
                closed = True
    finally:
        if hasattr(bus, "unsubscribe"):
            bus.unsubscribe(session_id, q)
