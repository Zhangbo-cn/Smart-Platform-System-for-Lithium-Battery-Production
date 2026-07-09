from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

logger = structlog.get_logger(__name__)

_current_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)

# 错误类型分类
# 注意顺序：子类在前，OSError 在后（PermissionError 是 OSError 的子类）
ERROR_TYPE_MAP: tuple[tuple[tuple[type[Exception], ...], str], ...] = (
    ((TimeoutError,), "timeout"),
    ((PermissionError,), "auth_fail"),
    ((ConnectionError, OSError), "network_err"),
    ((ValueError, TypeError, KeyError, AssertionError), "param_err"),
)


def classify_error(exc: Exception) -> str:
    """将异常分类为标准化 error_type。

    分类:
      - timeout:   超时类异常
      - network_err: 网络连接/IO异常
      - param_err:  参数/类型错误
      - auth_fail:  权限不足
      - inner_err:  其他内部错误（兜底）
    """
    for exc_types, err_type in ERROR_TYPE_MAP:
        if isinstance(exc, exc_types):
            return err_type
    return "inner_err"


def new_trace_id() -> str:
    return f"trc_{uuid.uuid4().hex[:16]}"


def get_trace_id() -> str | None:
    return _current_trace_id.get()


def set_trace_id(trace_id: str) -> None:
    _current_trace_id.set(trace_id)


class AuditTracer:
    """OTel-backed tracer. Falls back to structlog when OTel unavailable.

    Usage (unchanged):
        with tracer.span("tool.mes.query_batch_trace", attrs={...}):
            ...
    """

    def __init__(self) -> None:
        self._otel_tracer = otel_trace.get_tracer("harness-core") if _OTEL_AVAILABLE else None

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None):
        trace_id = get_trace_id()
        attrs = dict(attributes or {})

        if self._otel_tracer:
            # OTel span
            with self._otel_tracer.start_as_current_span(name) as span:
                span.set_attribute("trace_id", trace_id or "")
                for k, v in attrs.items():
                    span.set_attribute(k, str(v) if not isinstance(v, (int, float, bool)) else v)
                try:
                    yield
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.set_attribute("error.type", classify_error(exc))
                    raise
                else:
                    span.set_status(Status(StatusCode.OK))
        else:
            # 回退：structlog（同旧行为）
            span_id = uuid.uuid4().hex[:8]
            start = time.perf_counter()
            logger.info("span.start", span=name, span_id=span_id, trace_id=trace_id, **attrs)
            status = "ok"
            error_msg = None
            error_type = None
            try:
                yield
            except Exception as exc:
                status = "error"
                error_msg = str(exc)
                error_type = classify_error(exc)
                raise
            finally:
                duration_ms = int((time.perf_counter() - start) * 1000)
                logger.info(
                    "span.end",
                    span=name, span_id=span_id, trace_id=trace_id,
                    duration_ms=duration_ms, status=status,
                    error=error_msg, error_type=error_type,
                )
