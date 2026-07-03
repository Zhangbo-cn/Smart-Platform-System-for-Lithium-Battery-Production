from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_current_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return f"trc_{uuid.uuid4().hex[:16]}"


def get_trace_id() -> str | None:
    return _current_trace_id.get()


def set_trace_id(trace_id: str) -> None:
    _current_trace_id.set(trace_id)


class AuditTracer:
    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None):
        span_id = uuid.uuid4().hex[:8]
        start = time.perf_counter()
        trace_id = get_trace_id()
        attrs = attributes or {}
        logger.info("span.start", span=name, span_id=span_id, trace_id=trace_id, **attrs)
        status = "ok"
        error_msg = None
        try:
            yield
        except Exception as exc:
            status = "error"
            error_msg = str(exc)
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "span.end",
                span=name,
                span_id=span_id,
                trace_id=trace_id,
                duration_ms=duration_ms,
                status=status,
                error=error_msg,
            )
