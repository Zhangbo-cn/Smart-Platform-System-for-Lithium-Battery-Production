"""Trace Worker：批次追溯 + MCP（无 LLM）。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from python_a2a import Task

from harness_core.agent_bootstrap import bootstrap_agent_tools, register_with_registry
from harness_core.tool_registry import ToolRegistry
from platform_contracts.a2a_server import AsyncA2AServer
from platform_contracts.agent_handoffs import TraceRequest, TraceResponse
from platform_contracts.agent_registry_seed import TRACE_WORKER_CARD
from settings import get_settings
from trace_runner import run_trace

logger = structlog.get_logger(__name__)
_SERVICE = "trace-worker"


class TraceWorkerServer(AsyncA2AServer):
    def __init__(self, registry: ToolRegistry) -> None:
        super().__init__(TRACE_WORKER_CARD)
        self.registry = registry

    async def handle_task(self, task: Task) -> Task:
        payload = self.payload_from_task(task)
        req = TraceRequest(**payload)
        if not req.batch_id:
            raise ValueError("batch_id required")
        result = await run_trace(self.registry, req)
        return self.complete_task(task, result.model_dump(mode="json"))


class AppState:
    registry: ToolRegistry
    server: TraceWorkerServer
    mcp_clients: list
    mcp_connected: list[str]
    mcp_failed: list[str]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    registry = ToolRegistry()
    clients, connected, failed = await bootstrap_agent_tools(
        _SERVICE,
        registry,
        {"mes": settings.mcp_mes_url, "scada": settings.mcp_scada_url},
    )
    server = TraceWorkerServer(registry)
    state = AppState()
    state.registry = registry
    state.server = server
    state.mcp_clients = clients
    state.mcp_connected = connected
    state.mcp_failed = failed
    app.state.svc = state
    server.mount(app)
    # 注册到 Capability Registry
    await register_with_registry(
        registry_url=settings.registry_url,
        agent_name=_SERVICE,
        agent_description=TRACE_WORKER_CARD.description,
        agent_url="http://localhost:8002",
        capabilities=TRACE_WORKER_CARD.capabilities,
    )
    logger.info("trace_worker.startup", mcp_connected=connected, mcp_failed=failed)
    try:
        yield
    finally:
        for client in clients:
            await client.close()


app = FastAPI(title="trace-worker", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    svc = app.state.svc
    return {
        "status": "ok" if svc.mcp_connected else "degraded",
        "service": _SERVICE,
        "mcp_connected": svc.mcp_connected,
        "mcp_failed": svc.mcp_failed,
    }


@app.post("/v1/trace", response_model=TraceResponse)
async def trace_batch(req: TraceRequest) -> TraceResponse:
    if not req.batch_id:
        raise HTTPException(400, "batch_id required")
    svc = app.state.svc
    if not svc.mcp_connected:
        raise HTTPException(503, "no MCP servers connected")
    return await run_trace(svc.registry, req)
