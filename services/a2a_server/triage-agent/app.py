"""Triage Agent：意图识别 + 异常分诊（LLM + Rule 双模）。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from python_a2a import Task

from harness_core.agent_bootstrap import register_with_registry
from platform_contracts.a2a_server import AsyncA2AServer
from platform_contracts.agent_handoffs import TriageRequest, TriageResponse
from platform_contracts.agent_registry_seed import TRIAGE_AGENT_CARD
from settings import get_settings
from triage_engine import run_triage

logger = structlog.get_logger(__name__)
_SERVICE = "triage-agent"


class TriageAgentServer(AsyncA2AServer):
    def __init__(self) -> None:
        super().__init__(TRIAGE_AGENT_CARD)

    async def handle_task(self, task: Task) -> Task:
        payload = self.payload_from_task(task)
        req = TriageRequest(**payload)
        settings = get_settings()
        result = await run_triage(
            req,
            llm_api_key=settings.llm_api_key,
            llm_model=settings.llm_model,
            llm_base_url=settings.llm_base_url,
        )
        return self.complete_task(task, result.model_dump(mode="json"))


class AppState:
    server: TriageAgentServer


@asynccontextmanager
async def lifespan(app: FastAPI):
    server = TriageAgentServer()
    state = AppState()
    state.server = server
    app.state.svc = state
    server.mount(app)
    settings = get_settings()
    # 注册到 Capability Registry（非阻塞，失败不影响启动）
    registry_url = getattr(settings, "registry_url", "http://localhost:8021")
    await register_with_registry(
        registry_url=registry_url,
        agent_name=_SERVICE,
        agent_description=TRIAGE_AGENT_CARD.description,
        agent_url="http://localhost:8001",
        capabilities=TRIAGE_AGENT_CARD.capabilities,
    )
    logger.info("triage_agent.startup")
    try:
        yield
    finally:
        pass


app = FastAPI(title="triage-agent", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": _SERVICE}


# 非 A2A 快捷接口
@app.post("/v1/triage", response_model=TriageResponse)
async def triage(req: TriageRequest) -> TriageResponse:
    settings = get_settings()
    return await run_triage(
        req,
        llm_api_key=settings.llm_api_key,
        llm_model=settings.llm_model,
        llm_base_url=settings.llm_base_url,
    )
