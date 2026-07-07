"""Safety Agent — 停线改参门闩；独占 PLC MCP + HITL 签核。"""
from __future__ import annotations; from contextlib import asynccontextmanager; from typing import Any
import structlog; from fastapi import FastAPI; from python_a2a import Task
from harness_core.agent_bootstrap import bootstrap_agent_tools, register_with_registry; from harness_core.tool_registry import ToolRegistry
from platform_contracts.a2a_server import AsyncA2AServer; from platform_contracts.agent_registry_seed import SAFETY_AGENT_CARD as CARD
from platform_contracts.task_state import TaskState
logger=structlog.get_logger(__name__);S={"name":"safety-agent","port":8099,"mcp":{"plc":"","mes":"","qms":""}}

class Svr(AsyncA2AServer):
    def __init__(self,r:ToolRegistry)->None:super().__init__(CARD);self.r=r
    async def handle_task(self,t:Task)->Task:
        p=self.payload_from_task(t);action=p.get("action","")
        if action not in ("emergency_stop","write_setpoint","check_status"):
            return self.complete_task(t,{"error":f"unknown action:{action}"},state=TaskState.FAILED)
        # PLC 操作强制要求 HITL
        if action in ("emergency_stop","write_setpoint") and not p.get("hitl_approved"):
            return self.complete_task(t,{
                "requires_hitl":True,"action":action,
                "thread_id":str(t.session_id or t.id),
                "message":f"{action}需要安全主管签核",
                "params":{"line_id":p.get("line_id",""),"reason":p.get("reason","")}
            },state=TaskState.INPUT_REQUIRED)
        if action=="emergency_stop":
            r=await self.r.invoke("plc.emergency_stop",{"line_id":p["line_id"],"reason":p.get("reason",""),"operator_id":p.get("operator_id","safety-agent")},user_id=S["name"],user_role="safety_officer")
        elif action=="write_setpoint":
            r=await self.r.invoke("plc.write_setpoint",{"line_id":p["line_id"],"equipment_id":p["equipment_id"],"parameter":p["parameter"],"value":p["value"],"operator_id":p.get("operator_id","safety-agent"),"reason":p.get("reason","")},user_id=S["name"],user_role="safety_officer")
        else:
            r=await self.r.invoke("mes.get_process_params",{"batch_id":p.get("batch_id",""),"process_step":p.get("process_step","")},user_id=S["name"],user_role="safety_officer")
        return self.complete_task(t,{"action":action,"result":r,"hitl_approved":True})

@asynccontextmanager
async def lifespan(a:FastAPI):
    r=ToolRegistry();c,ok,fail=await bootstrap_agent_tools(S["name"],r,S["mcp"]);sv=Svr(r);a.state.svc=sv;sv.mount(a)
    await register_with_registry(registry_url="http://localhost:8021",agent_name=S["name"],agent_description=CARD.description,agent_url=f"http://localhost:{S['port']}",capabilities=CARD.capabilities)
    logger.info("safety.startup",mcp_connected=ok);yield
    for cl in c:await cl.close()
app=FastAPI(title=S["name"],lifespan=lifespan)
@app.get("/health")
async def health():return {"status":"ok","agent":S["name"]}
