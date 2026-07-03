-- Working Memory — 未结案工单持久化
-- 用于跨 session 持久化 PlatformContext，支撑"未结案工单"查询与恢复。
--
-- 应用层自動建表（session_store.py:PostgresSessionStore._ensure_table()），
-- 此 SQL 仅用于手动运维或 DBA 审核。
--
-- 连接信息（docker-compose）：
--   host: localhost  port: 5432
--   user: battery   password: battery   db: battery_agent

CREATE TABLE IF NOT EXISTS working_memory (
    session_id   TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL DEFAULT '',
    batch_id     TEXT NOT NULL DEFAULT '',
    task_status  TEXT NOT NULL DEFAULT '',
    context      JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_working_memory_task_status
    ON working_memory(task_status);

CREATE INDEX IF NOT EXISTS idx_working_memory_tenant_id
    ON working_memory(tenant_id);

CREATE INDEX IF NOT EXISTS idx_working_memory_updated_at
    ON working_memory(updated_at);

COMMENT ON TABLE working_memory IS 'PlatformContext 持久化 — 跨 A2A session 的 Working Memory';
COMMENT ON COLUMN working_memory.task_status IS 'running | hitl | completed | failed | cancelled';
COMMENT ON COLUMN working_memory.context IS 'PlatformContext 完整 JSON 序列化';
