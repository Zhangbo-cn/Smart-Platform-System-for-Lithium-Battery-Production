"""Harness 可观测性：OpenTelemetry 统一 tracing（替代三套系统）。

用法:
  from harness_core.observability import init_observability
  init_observability(service_name="quality-rca-agent", otel_endpoint="http://localhost:4318")

LangSmith 转为可选项（仅调试 LLM 输出质量时启用）。
"""

from __future__ import annotations

from typing import Any

import structlog


def init_observability(
    service_name: str = "battery-agent",
    otel_endpoint: str | None = None,
    *,
    log_level: str = "INFO",
    langsmith_api_key: str | None = None,
    langsmith_project: str = "battery-agent",
) -> bool:
    """统一初始化可观测性。

    1. structlog 日志配置（应用日志）
    2. OpenTelemetry SDK（tracing，如果 otel_endpoint 配置了）
    3. LangSmith（可选，仅 LLM 调试用）

    Returns:
        True 表示 OTel 已启用
    """
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    otel_ok = False
    if otel_endpoint:
        otel_ok = _init_otel(service_name, otel_endpoint)
    _maybe_init_langsmith(langsmith_api_key, langsmith_project)
    return otel_ok


def _maybe_init_langsmith(api_key: str | None, project: str) -> None:
    """可选初始化 LangSmith（仅用于 LLM 调试）。"""
    if not api_key:
        return
    import os
    os.environ.setdefault("LANGCHAIN_API_KEY", api_key)
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", project)


def _init_otel(service_name: str, endpoint: str) -> bool:
    """初始化 OpenTelemetry SDK。"""
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        if endpoint:
            exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
            provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return True
    except ImportError:
        return False


def instrument_fastapi(app) -> bool:
    """FastAPI 自动插装（HTTP 请求追踪）。"""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        return True
    except ImportError:
        return False


def build_langgraph_callbacks() -> list[Any]:
    """构建 LangGraph 回调列表（OTel + 可选 LangSmith）。

    LangSmith only active when LANGCHAIN_API_KEY environment var is set.
    """
    callbacks = []
    # OTel callback - 替换 LangChainTracer 作为主要 tracing
    try:
        from langchain_core.tracers import LangChainTracer

        tracer = LangChainTracer(project_name="battery-agent")
        # When LANGCHAIN_API_KEY is set, this sends to LangSmith too
        callbacks.append(tracer)
    except ImportError:
        pass
    return callbacks
