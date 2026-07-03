"""任务生命周期事件与 SSE 推送契约。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from platform_contracts.task_state import TaskState


class TaskEventType(StrEnum):
    CONNECTED = "connected"
    HEARTBEAT = "heartbeat"
    SUBMITTED = "task.submitted"
    RUNNING = "task.running"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    INPUT_REQUIRED = "task.input_required"
    COMPLETED = "task.completed"
    FAILED = "task.failed"
    CANCELLED = "task.cancelled"
    AGENT_DEGRADED = "agent.degraded"


TERMINAL_TASK_EVENTS: frozenset[TaskEventType] = frozenset(
    {
        TaskEventType.COMPLETED,
        TaskEventType.FAILED,
        TaskEventType.CANCELLED,
    }
)


class TaskEvent(BaseModel):
    seq: int
    event: TaskEventType
    session_id: str
    trace_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task_status: TaskState | None = None
    step: str | None = None
    agent: str | None = None
    message: str | None = None
    payload: dict[str, Any] | None = None

    def sse_event_name(self) -> str:
        return self.event.value

    def sse_data(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AgentHealthStatus(StrEnum):
    UNKNOWN = "unknown"
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class AgentHealthRecord(BaseModel):
    name: str
    status: AgentHealthStatus = AgentHealthStatus.UNKNOWN
    url: str
    last_probe_at: datetime | None = None
    last_ok_at: datetime | None = None
    consecutive_failures: int = 0
    latency_ms: float | None = None
    detail: str | None = None
