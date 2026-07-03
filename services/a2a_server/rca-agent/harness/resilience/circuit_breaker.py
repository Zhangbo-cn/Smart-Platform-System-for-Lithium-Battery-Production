from __future__ import annotations

import asyncio
import time
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class CircuitOpenError(Exception):
    pass


class _State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_seconds: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._state = _State.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._lock = asyncio.Lock()

    async def call(self, fn, *args, **kwargs):
        async with self._lock:
            if self._state is _State.OPEN:
                if time.time() - self._opened_at >= self.recovery_seconds:
                    self._state = _State.HALF_OPEN
                else:
                    raise CircuitOpenError(f"Circuit '{self.name}' is open")

        try:
            result = await fn(*args, **kwargs)
        except Exception:
            await self._on_failure()
            raise
        await self._on_success()
        return result

    async def _on_success(self) -> None:
        async with self._lock:
            self._failures = 0
            if self._state is not _State.CLOSED:
                logger.info("circuit.closed", name=self.name)
            self._state = _State.CLOSED

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = _State.OPEN
                self._opened_at = time.time()
                logger.warning("circuit.opened", name=self.name, failures=self._failures)
