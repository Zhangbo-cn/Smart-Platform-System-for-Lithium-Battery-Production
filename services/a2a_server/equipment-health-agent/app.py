"""Equipment Health Agent — 设备预测性维护。"""
from __future__ import annotations; from contextlib import asynccontextmanager
import structlog; from fastapi import FastAPI; from python_a2a import Task
from harness_core.agent_bootstrap import bootstrap_agent_tools, register_with_registry; from harness_core.tool_registry import ToolRegistry
from platform_contracts.a2a_server import AsyncA2AServer; from platform_contracts.agent_registry_seed import EQUIPMENT_AGENT_CARD as CARD
logger=structlog.get_logger(__name__);S={"name":"equipment-health-agent","port":8203,"mcp":{"scada":"","eam":""}}
class Svr(AsyncA2AServer):
    def __init__(self,r:ToolRegistry)->None:super().__init__(CARD);self.r=r
    async def handle_task(self,t:Task)->Task:
        p=self.payload_from_task(t);eid=p.get("equipment_id","COAT-A2")
        ts=await self.r.invoke("scada.query_equipment_timeseries",{"equipment_id":eid,"sensor_tags":["temperature","pressure","speed"],"start_time":p.get("start",""),"end_time":p.get("end","")},user_id=S["name"],user_role="maintenance_engineer")
        logs=await self.r.invoke("eam.get_maintenance_log",{"equipment_id":eid},user_id=S["name"],user_role="maintenance_engineer")
        return self.complete_task(t,{"equipment_id":eid,"timeseries":ts,"maintenance_logs":logs})
@asynccontextmanager
async def lifespan(a:FastAPI):
    r=ToolRegistry();c,ok,fail=await bootstrap_agent_tools(S["name"],r,S["mcp"]);sv=Svr(r);a.state.svc=sv;sv.mount(a)
    await register_with_registry(registry_url="http://localhost:8021",agent_name=S["name"],agent_description=CARD.description,agent_url=f"http://localhost:{S['port']}",capabilities=CARD.capabilities)
    logger.info("eqhealth.startup",mcp_connected=ok);yield
    for cl in c:await cl.close()
app=FastAPI(title=S["name"],lifespan=lifespan)
@app.get("/health")
async def health():return {"status":"ok","agent":S["name"]}
