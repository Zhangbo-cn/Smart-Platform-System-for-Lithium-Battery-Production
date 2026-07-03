"""Agent 标准化模板：A2A + MCP + Registry + HITL 统一接入。

使用方式：
  1. 复制本文件到 services/<your-agent>/app.py
  2. 配置 AgentConfig
  3. 实现 _execute() 方法
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from python_a2a import Task

from harness_core.agent_bootstrap import bootstrap_agent_tools, register_with_registry
from harness_core.tool_registry import ToolRegistry
from platform_contracts.a2a_server import AsyncA2AServer
from platform_contracts.agent_card import AgentCard, AgentSkill


# ── 配置（子类修改此节）──


class AgentConfig(BaseModel):
    """Agent 元配置——修改这里定义你的 Agent。"""
    name: str = "example-agent"
    description: str = "示例 Agent，替换为实际描述"
    port: int = 8200
    capabilities: list[str] = ["example_capability"]
    mcp_servers: dict[str, str] = {}  # {"server_name": "http://localhost:XXXX/sse"}
    agent_url: str = "http://localhost:8200"


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    mcp_mes_url: str = ""
    mcp_scada_url: str = ""
    registry_url: str = "http://localhost:8021"
    http_timeout: float = 30.0


def _build_agent_card(cfg: AgentConfig) -> AgentCard:
    return AgentCard(
        name=cfg.name,
        description=cfg.description,
        url=f"{cfg.agent_url}/a2a/v1",
        version="1.0.0",
        capabilities=cfg.capabilities,
        skills=[AgentSkill(id=cap, name=cap, description=f"{cfg.name} capability: {cap}") for cap in cfg.capabilities],
        mcp_servers=list(cfg.mcp_servers.keys()),
        enabled=True,
    )


# ── 模板 Agent Server ──


class StandardAgentServer(AsyncA2AServer):
    """标准 A2A Agent 模板。

    子类只需 override：
    - _execute(payload) -> dict  # 业务逻辑
    - _validate(payload)         # 可选，校验入参
    """

    def __init__(self, card: AgentCard, registry: ToolRegistry) -> None:
        super().__init__(card)
        self.registry = registry
        self.card = card

    def _validate(self, payload: dict[str, Any]) -> None:
        """校验请求参数。子类可选 override。"""
        pass

    async def _execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """执行业务逻辑。子类必须 override。"""
        raise NotImplementedError("子类必须实现 _execute()")

    async def handle_task(self, task: Task) -> Task:
        payload = self.payload_from_task(task)
        self._validate(payload)
        result = await self._execute(payload)
        return self.complete_task(task, result)


# ── App + Lifespan ──


class AppState:
    registry: ToolRegistry
    server: StandardAgentServer
    mcp_clients: list
    mcp_connected: list[str]
    mcp_failed: list[str]


def create_app(cfg: AgentConfig) -> FastAPI:
    """工厂函数：创建配置好的 FastAPI app。"""
    logger = structlog.get_logger(__name__)
    card = _build_agent_card(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = AgentSettings()
        tool_registry = ToolRegistry()
        server_urls = {k: v for k, v in cfg.mcp_servers.items()}
        clients, connected, failed = await bootstrap_agent_tools(
            cfg.name,
            tool_registry,
            server_urls,
        )
        server = StandardAgentServer(card, tool_registry)
        state = AppState()
        state.registry = tool_registry
        state.server = server
        state.mcp_clients = clients
        state.mcp_connected = connected
        state.mcp_failed = failed
        app.state.svc = state
        server.mount(app)

        # 注册到 Capability Registry
        await register_with_registry(
            registry_url=settings.registry_url,
            agent_name=cfg.name,
            agent_description=card.description,
            agent_url=cfg.agent_url,
            capabilities=card.capabilities,
        )

        logger.info("agent.startup", name=cfg.name, mcp_connected=connected, mcp_failed=failed)
        try:
            yield
        finally:
            for c in clients:
                await c.close()

    app = FastAPI(title=cfg.name, version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        svc = app.state.svc
        return {
            "status": "ok" if svc.mcp_connected else "degraded",
            "service": cfg.name,
            "mcp_connected": svc.mcp_connected,
            "mcp_failed": svc.mcp_failed,
        }

    return app
