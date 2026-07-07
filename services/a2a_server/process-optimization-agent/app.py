"""Process Optimization Agent — 工艺参数建议。"""
from __future__ import annotations; from contextlib import asynccontextmanager; from typing import Any
import structlog; from fastapi import FastAPI; from python_a2a import Task
from harness_core.agent_bootstrap import bootstrap_agent_tools, register_with_registry; from harness_core.tool_registry import ToolRegistry
from platform_contracts.a2a_server import AsyncA2AServer; from platform_contracts.agent_registry_seed import PROCESS_AGENT_CARD as CARD
logger = structlog.get_logger(__name__)
S = {"name":"process-optimization-agent","port":8202,"mcp":{"mes":"","scada":"","knowledge":""}}
class Svr(AsyncA2AServer):
    def __init__(self,r:ToolRegistry)->None: super().__init__(CARD);self.r=r
    async def handle_task(self,t:Task)->Task:
        p=self.payload_from_task(t);step=p.get("process_step","coating")
        params=await self.r.invoke("mes.get_process_params",{"batch_id":p.get("batch_id",""),"process_step":step},user_id=S["name"],user_role="process_engineer")
        sop=await self.r.invoke("knowledge.search_sop",{"defect_type":p.get("defect_type","")},user_id=S["name"],user_role="process_engineer")
        return self.complete_task(t,{"params":params,"sop_reference":sop,"suggestions":self._suggest(params,step)})
    @staticmethod
    def _suggest(params:Any,step:str)->list[str]:
        if not isinstance(params,dict):return ["参数数据不可用"]
        p=params.get("params",{});sugs=[]
        if p.get("coating_thickness_std_um",0)>3:sugs.append(f"涂布厚度波动偏大(std={p.get('coating_thickness_std_um')}μm)，建议检查刮刀状态")
        if p.get("coating_speed_m_min",0)>30:sugs.append(f"涂布速度偏高({p.get('coating_speed_m_min')}m/min)，建议降速提升一致性")
        return sugs or ["当前参数在规格范围内"]
@asynccontextmanager
async def lifespan(a:FastAPI):
    r=ToolRegistry();c,ok,fail=await bootstrap_agent_tools(S["name"],r,S["mcp"]);sv=Svr(r);a.state.svc=sv;sv.mount(a)
    await register_with_registry(registry_url="http://localhost:8021",agent_name=S["name"],agent_description=CARD.description,agent_url=f"http://localhost:{S['port']}",capabilities=CARD.capabilities)
    logger.info("procopt.startup",mcp_connected=ok);yield
    for cl in c:await cl.close()
app=FastAPI(title=S["name"],lifespan=lifespan)
@app.get("/health")
async def health():return {"status":"ok","agent":S["name"]}
