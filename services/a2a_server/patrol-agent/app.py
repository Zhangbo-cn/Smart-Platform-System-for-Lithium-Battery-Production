"""Patrol Agent — 开班巡线摘要。"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any
import structlog
from fastapi import FastAPI
from python_a2a import Task
from harness_core.agent_bootstrap import bootstrap_agent_tools, register_with_registry
from harness_core.tool_registry import ToolRegistry
from platform_contracts.a2a_server import AsyncA2AServer
from platform_contracts.agent_registry_seed import PATROL_AGENT_CARD as AGENT_CARD
logger = structlog.get_logger(__name__)
_SETTINGS = {"name": "patrol-agent", "port": 8005, "mcp": {"mes": "", "scada": ""}}

class AgentServer(AsyncA2AServer):
    def __init__(self, registry: ToolRegistry) -> None:
        super().__init__(AGENT_CARD); self.registry = registry
    async def handle_task(self, task: Task) -> Task:
        p = self.payload_from_task(task)
        s = await self.registry.invoke("mes.get_shift_summary", {"line_id": p.get("line_id","LINE-1"),"shift":p.get("shift","D")}, user_id="patrol-agent",user_role="quality_manager")
        d = await self.registry.invoke("mes.query_defect_cells", {"start_time":"","end_time":"","defect_type":"","line_id":p.get("line_id","LINE-1")}, user_id="patrol-agent",user_role="quality_manager")
        return self.complete_task(task, {"shift_summary":s,"defect_cells":d})

@asynccontextmanager
async def lifespan(app: FastAPI):
    r = ToolRegistry(); c, ok, fail = await bootstrap_agent_tools(_SETTINGS["name"], r, _SETTINGS["mcp"])
    s = AgentServer(r); app.state.svc = s; s.mount(app)
    await register_with_registry(registry_url="http://localhost:8021", agent_name=_SETTINGS["name"], agent_description=AGENT_CARD.description, agent_url=f"http://localhost:{_SETTINGS['port']}", capabilities=AGENT_CARD.capabilities)
    logger.info("patrol.startup", mcp_connected=ok, mcp_failed=fail)
    try: yield
    finally:
        for cl in c: await cl.close()

app = FastAPI(title=_SETTINGS["name"], lifespan=lifespan)
@app.get("/health")
async def health(): return {"status":"ok" if not hasattr(app.state.svc,'mcp_failed') or not app.state.svc.mcp_failed else "degraded","agent":_SETTINGS["name"]}
