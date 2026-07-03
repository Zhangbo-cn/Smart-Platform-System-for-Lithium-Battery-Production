"""LangSmith / LangChain 可观测性回调。"""

from __future__ import annotations

import os
from typing import Any

from config import get_settings


def build_langsmith_callbacks() -> list[Any]:
    """若配置了 LANGSMITH_API_KEY，返回 LangChain tracer 回调列表。"""
    settings = get_settings()
    if not settings.langsmith_api_key:
        return []

    os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)

    try:
        from langchain_core.tracers import LangChainTracer

        return [LangChainTracer(project_name=settings.langsmith_project)]
    except ImportError:
        return []
