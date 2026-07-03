"""LangGraph checkpointer 工厂：dev 用内存，生产可切 Redis。"""

from __future__ import annotations

from typing import Any

import structlog
from langgraph.checkpoint.memory import MemorySaver

from config import get_settings

logger = structlog.get_logger(__name__)


def build_checkpointer() -> Any:
    settings = get_settings()
    backend = settings.langgraph_checkpoint_backend.lower()

    if backend == "memory":
        logger.info("checkpoint.backend", backend="memory")
        return MemorySaver()

    if backend == "redis":
        try:
            from langgraph.checkpoint.redis import RedisSaver
        except ImportError as exc:
            raise RuntimeError(
                "LANGGRAPH_CHECKPOINT_BACKEND=redis requires "
                "'langgraph-checkpoint-redis'. "
                "Install: pip install -e services/rca-agent[redis-checkpoint]"
            ) from exc
        saver = RedisSaver.from_conn_string(settings.redis_url)
        # 初始化 Redis 索引结构（幂等）
        if hasattr(saver, "setup"):
            saver.setup()
        logger.info("checkpoint.backend", backend="redis", url=settings.redis_url)
        return saver

    raise ValueError(
        f"Unknown langgraph_checkpoint_backend={backend!r}; use 'memory' or 'redis'"
    )
