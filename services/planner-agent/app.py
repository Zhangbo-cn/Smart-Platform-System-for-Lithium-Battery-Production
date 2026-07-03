"""Planner Agent：NL → playbook + params（ReAct + Tool Use）。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from pydantic import BaseModel
from python_a2a import Task

from harness_core.agent_bootstrap import register_with_registry
from platform_contracts.a2a_server import AsyncA2AServer
from platform_contracts.agent_registry_seed import PLANNER_CARD
from platform_contracts.plan_result import PlanResult, PlannerRequest
from plan_engine import get_service_card, list_playbooks, plan as rule_plan
from planning_tools import refresh_playbooks_from_registry
from react_agent import plan_with_react
from settings import get_settings

logger = structlog.get_logger(__name__)

_SERVICE = "planner"


class PlanResponse(BaseModel):
    result: PlanResult


class PlannerServer(AsyncA2AServer):
    async def handle_task(self, task: Task) -> Task:
        req = PlannerRequest(**self.payload_from_task(task))
        settings = get_settings()
        if settings.planner_mode == "react":
            result = await plan_with_react(req, settings)
        else:
            result = rule_plan(req)
        return self.complete_task(task, result.model_dump(mode="json"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await register_with_registry(
        registry_url=settings.registry_url,
        agent_name=_SERVICE,
        agent_description=PLANNER_CARD.description,
        agent_url="http://localhost:8011",
        capabilities=PLANNER_CARD.capabilities,
    )
    # 从 Registry 刷新动态 playbook 列表
    await refresh_playbooks_from_registry(settings.registry_url)
    logger.info("planner.startup", registry=settings.registry_url)
    yield


planner_server = PlannerServer(PLANNER_CARD)
app = FastAPI(title="planner", version="0.2.0", lifespan=lifespan)
planner_server.mount(app)


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    mode = settings.planner_mode
    llm = "configured" if settings.llm_base_url and settings.llm_api_key else "missing"
    return {"status": "ok", "service": "planner", "mode": mode, "llm": llm}


@app.get("/v1/playbooks")
def playbooks() -> dict:
    return {"playbooks": list_playbooks()}


@app.get("/v1/services/{name}/card")
def service_card(name: str) -> dict:
    return get_service_card(name)


@app.post("/v1/plan", response_model=PlanResponse)
async def create_plan(req: PlannerRequest) -> PlanResponse:
    settings = get_settings()
    if settings.planner_mode == "react":
        result = await plan_with_react(req, settings)
    else:
        result = rule_plan(req)
    return PlanResponse(result=result)
