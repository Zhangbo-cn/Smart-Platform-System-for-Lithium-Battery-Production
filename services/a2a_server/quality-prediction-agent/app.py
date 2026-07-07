"""Quality Prediction Agent — SPC 预警 + 缺陷趋势。"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any
import structlog
from fastapi import FastAPI
from python_a2a import Task
from harness_core.agent_bootstrap import bootstrap_agent_tools, register_with_registry
from harness_core.tool_registry import ToolRegistry
from platform_contracts.a2a_server import AsyncA2AServer
from platform_contracts.agent_registry_seed import QUALITY_PRED_AGENT_CARD as CARD
logger = structlog.get_logger(__name__)
S = {"name": "quality-prediction-agent", "port": 8201, "mcp": {"mes": "", "scada": "", "lims": ""}}

class Svr(AsyncA2AServer):
    def __init__(self, r: ToolRegistry) -> None: super().__init__(CARD); self.r = r
    async def handle_task(self, t: Task) -> Task:
        p = self.payload_from_task(t)
        cells = await self.r.invoke("mes.query_defect_cells", {"start_time":p.get("start",""),"end_time":p.get("end",""),"defect_type":p.get("defect_type",""),"line_id":p.get("line_id","LINE-1")}, user_id=S["name"],user_role="quality_manager")
        trend = await self.r.invoke("lims.batch_test_summary", {"batch_id":p.get("batch_id","")}, user_id=S["name"],user_role="quality_manager") if p.get("batch_id") else {}
        return self.complete_task(t, {"defect_cells":cells,"trend":trend,"defect_type":p.get("defect_type")})

@asynccontextmanager
async def lifespan(app: FastAPI):
    r = ToolRegistry(); c, ok, fail = await bootstrap_agent_tools(S["name"], r, S["mcp"])
    sv = Svr(r); app.state.svc = sv; sv.mount(app)
    await register_with_registry(registry_url="http://localhost:8021", agent_name=S["name"], agent_description=CARD.description, agent_url=f"http://localhost:{S['port']}", capabilities=CARD.capabilities)
    logger.info("qpred.startup", mcp_connected=ok)
    try: yield
    finally:
        for cl in c: await cl.close()

app = FastAPI(title=S["name"], lifespan=lifespan)
@app.get("/health")
async def health(): return {"status":"ok","agent":S["name"]}
