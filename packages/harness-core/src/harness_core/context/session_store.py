"""PlatformContext Session 存储：memory | redis | postgres。"""

from __future__ import annotations

import json
from typing import Literal, Protocol

import structlog

from platform_contracts.platform_context import PlatformContext

logger = structlog.get_logger(__name__)

ContextBackend = Literal["memory", "redis", "postgres"]


class SessionStore(Protocol):
    async def get(self, session_id: str) -> PlatformContext | None: ...
    async def save(self, ctx: PlatformContext) -> None: ...
    async def exists(self, session_id: str) -> bool: ...
    async def list_active(self, tenant_id: str | None = None) -> list[PlatformContext]: ...


class MemorySessionStore:
    def __init__(self) -> None:
        self._data: dict[str, PlatformContext] = {}

    async def get(self, session_id: str) -> PlatformContext | None:
        return self._data.get(session_id)

    async def save(self, ctx: PlatformContext) -> None:
        self._data[ctx.session_id] = ctx

    async def exists(self, session_id: str) -> bool:
        return session_id in self._data

    async def list_active(self, tenant_id: str | None = None) -> list[PlatformContext]:
        return [
            ctx
            for ctx in self._data.values()
            if ctx.task_status not in ("completed", "failed", "cancelled")
            and (tenant_id is None or ctx.tenant_id == tenant_id)
        ]


class RedisSessionStore:
    def __init__(self, redis_url: str, ttl_seconds: int = 86_400) -> None:
        self._redis_url = redis_url
        self._ttl = ttl_seconds
        self._client = None

    async def _client_or_connect(self):
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._redis_url, decode_responses=True)
        return self._client

    @staticmethod
    def _key(session_id: str) -> str:
        return f"platform:context:{session_id}"

    async def get(self, session_id: str) -> PlatformContext | None:
        client = await self._client_or_connect()
        raw = await client.get(self._key(session_id))
        if not raw:
            return None
        return PlatformContext.model_validate_json(raw)

    async def save(self, ctx: PlatformContext) -> None:
        client = await self._client_or_connect()
        await client.setex(self._key(ctx.session_id), self._ttl, ctx.model_dump_json())

    async def exists(self, session_id: str) -> bool:
        client = await self._client_or_connect()
        return bool(await client.exists(self._key(session_id)))

    async def list_active(self, tenant_id: str | None = None) -> list[PlatformContext]:
        """Redis 不适合全量 scan，标记为未实现。"""
        raise NotImplementedError("list_active not supported for Redis backend")


class PostgresSessionStore:
    """PostgreSQL 持久化 Session Store — 承载 Working Memory（未结案工单）。

    Schema: working_memory
      session_id  TEXT PRIMARY KEY  — A2A session 标识
      tenant_id   TEXT              — 租户/工厂隔离
      batch_id    TEXT              — 批次号，便于按批次检索
      task_status TEXT              — 当前状态，索引
      context     JSONB             — 完整 PlatformContext 序列化
      created_at  TIMESTAMPTZ       — 首次创建时间
      updated_at  TIMESTAMPTZ       — 最后更新时间

    未结案工单 = task_status NOT IN ('completed', 'failed', 'cancelled')。
    TTL 清理由外部 cron 或 app 层定时调用 cleanup_expired() 完成。
    """

    def __init__(
        self,
        dsn: str,
        *,
        table_name: str = "working_memory",
        ttl_seconds: int = 7 * 86_400,  # 默认 7 天
    ) -> None:
        self._dsn = dsn
        self._table = table_name
        self._ttl = ttl_seconds
        self._pool = None

    async def _pool_or_create(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=1,
                max_size=4,
                command_timeout=10,
            )
        return self._pool

    async def _ensure_table(self):
        """自动建表（幂等）。首次启动无需手动跑 migration。"""
        pool = await self._pool_or_create()
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    session_id   TEXT PRIMARY KEY,
                    tenant_id    TEXT NOT NULL DEFAULT '',
                    batch_id     TEXT NOT NULL DEFAULT '',
                    task_status  TEXT NOT NULL DEFAULT '',
                    context      JSONB NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_{self._table}_task_status
                    ON {self._table}(task_status);
                CREATE INDEX IF NOT EXISTS idx_{self._table}_tenant_id
                    ON {self._table}(tenant_id);
                CREATE INDEX IF NOT EXISTS idx_{self._table}_updated_at
                    ON {self._table}(updated_at);
                """
            )
            logger.info("postgres_store.table_ready", table=self._table)

    async def get(self, session_id: str) -> PlatformContext | None:
        pool = await self._pool_or_create()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT context FROM {self._table} WHERE session_id = $1",
                session_id,
            )
        if not row:
            return None
        return PlatformContext.model_validate_json(row["context"])

    async def save(self, ctx: PlatformContext) -> None:
        pool = await self._pool_or_create()
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table} (session_id, tenant_id, batch_id, task_status, context, updated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
                ON CONFLICT (session_id)
                DO UPDATE SET
                    tenant_id   = EXCLUDED.tenant_id,
                    batch_id    = EXCLUDED.batch_id,
                    task_status = EXCLUDED.task_status,
                    context     = EXCLUDED.context,
                    updated_at  = NOW()
                """,
                ctx.session_id,
                ctx.tenant_id or "",
                ctx.batch_id or "",
                ctx.task_status or "",
                ctx.model_dump_json(),
            )

    async def exists(self, session_id: str) -> bool:
        pool = await self._pool_or_create()
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                f"SELECT 1 FROM {self._table} WHERE session_id = $1",
                session_id,
            )
        return row is not None

    async def list_active(self, tenant_id: str | None = None) -> list[PlatformContext]:
        """列出未结案工单 (task_status NOT IN completed/failed/cancelled)。"""
        pool = await self._pool_or_create()
        if tenant_id:
            rows = await pool.fetch(
                f"SELECT context FROM {self._table} "
                "WHERE task_status NOT IN ('completed','failed','cancelled') "
                "AND tenant_id = $1 ORDER BY updated_at DESC",
                tenant_id,
            )
        else:
            rows = await pool.fetch(
                f"SELECT context FROM {self._table} "
                "WHERE task_status NOT IN ('completed','failed','cancelled') "
                "ORDER BY updated_at DESC"
            )
        return [PlatformContext.model_validate_json(r["context"]) for r in rows]

    async def cleanup_expired(self) -> int:
        """删除超过 TTL 的已完结工单。返回删除行数。"""
        pool = await self._pool_or_create()
        result = await pool.execute(
            f"DELETE FROM {self._table} "
            "WHERE updated_at < NOW() - make_interval(secs => $1) "
            "AND task_status IN ('completed','failed','cancelled')",
            self._ttl,
        )
        # asyncpg returns "DELETE N" string
        count = int(result.replace("DELETE ", "")) if result.startswith("DELETE ") else 0
        if count:
            logger.info("postgres_store.cleanup", deleted=count, ttl_seconds=self._ttl)
        return count


def build_session_store(
    backend: ContextBackend,
    *,
    redis_url: str = "redis://localhost:6379/0",
    ttl_seconds: int = 86_400,
    postgres_dsn: str = "postgresql://postgres:postgres@localhost:5432/platform",
) -> SessionStore:
    if backend == "redis":
        logger.info("session_store.backend", backend="redis", url=redis_url, ttl=ttl_seconds)
        return RedisSessionStore(redis_url, ttl_seconds)
    if backend == "postgres":
        logger.info("session_store.backend", backend="postgres", dsn=postgres_dsn, ttl=ttl_seconds)
        store = PostgresSessionStore(postgres_dsn, ttl_seconds=ttl_seconds)
        # 首次使用自动建表，不阻塞 app 启动
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(store._ensure_table())
        except RuntimeError:
            pass
        return store
    logger.info("session_store.backend", backend="memory")
    return MemorySessionStore()
