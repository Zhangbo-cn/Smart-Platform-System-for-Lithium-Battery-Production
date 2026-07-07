"""WMS Supply Agent — 仓储与物料追溯。"""
from __future__ import annotations; from contextlib import asynccontextmanager
import structlog; from fastapi import FastAPI; from python_a2a import Task
from harness_core.agent_bootstrap import bootstrap_agent_tools, register_with_registry; from harness_core.tool_registry import ToolRegistry
from platform_contracts.a2a_server import AsyncA2AServer; from platform_contracts.agent_registry_seed import WMS_AGENT_CARD as CARD
logger=structlog.get_logger(__name__);S={"name":"wms-supply-agent","port":8204,"mcp":{"wms":"","erp":""}}
class Svr(AsyncA2AServer):
    def __init__(self,r:ToolRegistry)->None:super().__init__(CARD);self.r=r
    async def handle_task(self,t:Task)->Task:
        p=self.payload_from_task(t);mat=p.get("material_code","NMC-811")
        inv=await self.r.invoke("wms.get_inventory",{"material_code":mat},user_id=S["name"],user_role="logistics")
        trace=await self.r.invoke("wms.trace_material_location",{"material_code":mat},user_id=S["name"],user_role="logistics")
        supp=await self.r.invoke("erp.query_material_batch",{"material_batch_id":p.get("batch_id","")},user_id=S["name"],user_role="logistics") if p.get("batch_id") else {}
        return self.complete_task(t,{"inventory":inv,"trace":trace,"supplier":supp})
@asynccontextmanager
async def lifespan(a:FastAPI):
    r=ToolRegistry();c,ok,fail=await bootstrap_agent_tools(S["name"],r,S["mcp"]);sv=Svr(r);a.state.svc=sv;sv.mount(a)
    await register_with_registry(registry_url="http://localhost:8021",agent_name=S["name"],agent_description=CARD.description,agent_url=f"http://localhost:{S['port']}",capabilities=CARD.capabilities)
    logger.info("wms.startup",mcp_connected=ok);yield
    for cl in c:await cl.close()
app=FastAPI(title=S["name"],lifespan=lifespan)
@app.get("/health")
async def health():return {"status":"ok","agent":S["name"]}
