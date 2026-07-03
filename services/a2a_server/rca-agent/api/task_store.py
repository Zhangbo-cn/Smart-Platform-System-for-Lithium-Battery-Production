"""A2A Task 快照存储（进程内；生产可换 Redis）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from api.schemas import AnalysisResponse


@dataclass
class StoredTask:
    task_id: str
    task_dict: dict[str, Any]
    analysis: AnalysisResponse
    status: str
    cancelled: bool = False


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, StoredTask] = {}

    def save(
        self,
        *,
        task_id: str,
        task_dict: dict[str, Any],
        analysis: AnalysisResponse,
        status: str,
    ) -> None:
        self._tasks[task_id] = StoredTask(
            task_id=task_id,
            task_dict=task_dict,
            analysis=analysis,
            status=status,
        )

    def get(self, task_id: str) -> StoredTask | None:
        return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> StoredTask | None:
        stored = self._tasks.get(task_id)
        if stored is None:
            return None
        stored.cancelled = True
        stored.status = "cancelled"
        return stored

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
