"""Reporter Agent：Deep Agents 动态 8D + QMS 写回（模板 fallback）。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from python_a2a import Task

from deep_agent_runner import run_report_with_deep_agent
from harness_core.agent_bootstrap import bootstrap_agent_tools, register_with_registry
from harness_core.tool_registry import ToolRegistry
from platform_contracts.a2a_server import AsyncA2AServer
from platform_contracts.agent_handoffs import Report8dRequest, Report8dResponse
from platform_contracts.agent_registry_seed import REPORT_REPORTER_AGENT_CARD
from report_runner import run_report_8d
from settings import get_settings

logger = structlog.get_logger(__name__)
_SERVICE = "report-reporter-agent"


class ReportReporterServer(AsyncA2AServer):
    def __init__(self, registry: ToolRegistry) -> None:
        super().__init__(REPORT_REPORTER_AGENT_CARD)
        self.registry = registry

    async def handle_task(self, task: Task) -> Task:
        payload = self.payload_from_task(task)
        req = Report8dRequest(**payload)
        if not req.hitl_approved:
            raise ValueError("hitl_approved required")
        if not req.root_cause:
            raise ValueError("root_cause required")

        settings = get_settings()
        report_md, recommendations, capa_id, qms_status, mode = await run_report_with_deep_agent(
            self.registry, req, settings
        )
        return self.complete_task(
            task,
            Report8dResponse(
                report_md=report_md,
                recommendations=recommendations,
                stub=False,
                capa_id=capa_id,
                qms_status=qms_status,
                generation_mode=mode,
            ).model_dump(mode="json"),
        )


class AppState:
    registry: ToolRegistry
    server: ReportReporterServer
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
        {"qms": settings.mcp_qms_url},
    )
    server = ReportReporterServer(registry)
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
        agent_description=REPORT_REPORTER_AGENT_CARD.description,
        agent_url="http://localhost:8004",
        capabilities=REPORT_REPORTER_AGENT_CARD.capabilities,
    )
    logger.info(
        "report_reporter_agent.startup",
        mode=settings.reporter_mode,
        mcp_connected=connected,
        mcp_failed=failed,
    )
    try:
        yield
    finally:
        for client in clients:
            await client.close()


app = FastAPI(title="report-reporter-agent", version="0.4.0", lifespan=lifespan)


@app.get("/health")
def health():
    svc = app.state.svc
    settings = get_settings()
    return {
        "status": "ok" if svc.mcp_connected else "degraded",
        "service": _SERVICE,
        "reporter_mode": settings.reporter_mode,
        "mcp_connected": svc.mcp_connected,
        "mcp_failed": svc.mcp_failed,
    }


@app.post("/v1/report/8d", response_model=Report8dResponse)
async def report_8d(req: Report8dRequest) -> Report8dResponse:
    if not req.hitl_approved:
        raise HTTPException(400, "hitl_approved required")
    if not req.root_cause:
        raise HTTPException(400, "root_cause required")

    svc = app.state.svc
    settings = get_settings()
    try:
        report_md, recommendations, capa_id, qms_status, mode = await run_report_with_deep_agent(
            svc.registry, req, settings
        )
    except Exception as exc:
        logger.exception("report_8d.failed", session_id=req.session_id, error=str(exc))
        report_md, recommendations, capa_id, qms_status = await run_report_8d(svc.registry, req)
        mode = "template"

    return Report8dResponse(
        report_md=report_md,
        recommendations=recommendations,
        stub=False,
        capa_id=capa_id,
        qms_status=qms_status,
        generation_mode=mode,
    )
