"""RCA Agent：A2A + LangGraph 根因分析 — AsyncA2AServer 统一适配。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from python_a2a import Task, TaskState as A2ATaskState, TaskStatus

from agent.graphs import build_quality_analysis_graph
from agent.tools.bootstrap import bootstrap_registry
from agent.tools.registry import ToolRegistry
from api.auth import TokenPayload, decode_token
from api.handlers import prior_evidence_items, prior_tool_records
from api.schemas import AnalysisResponse, AnalysisRequest, HITLResolveRequest
from api.tracing import build_langsmith_callbacks
from config import get_settings
from harness.checkpoint import build_checkpointer
from harness_core.agent_bootstrap import register_with_registry
from harness_core.audit.tracer import new_trace_id, set_trace_id
from harness_core.permission.checker import PermissionChecker
from harness.context.memory_harness import MemoryHarness
from knowledge.fmea_registry import FMEARegistry
from langgraph.types import Command as LangGraphCommand
from platform_contracts.a2a_server import AsyncA2AServer
from platform_contracts.agent_card import RCA_AGENT_CARD
from platform_contracts.task_state import TaskState

logger = structlog.get_logger(__name__)


# ── RcaAgentServer — A2A + LangGraph ───────────────────────────────


class RcaAgentServer(AsyncA2AServer):
    """RCA Agent 服务器。

    build_input → [handle_task 自动: graph.ainvoke → interrupt 捕获] → extract_output

    与 AsyncA2AServer 的区别：
    - build_input 中注入 memory_context + 转换 prior_evidence
    - handle_task 覆写加入超时 + memory.persist
    - extract_output 包装为 AnalysisResponse 格式
    """

    def __init__(
        self,
        card,
        memory: MemoryHarness,
        registry: ToolRegistry,
        graph,
        *,
        checkpointer=None,
        redis_url: str | None = None,
    ) -> None:
        super().__init__(card, redis_url=redis_url)
        self._memory = memory
        self._registry = registry
        self._graph = graph
        self._checkpointer = checkpointer
        # A2A 路径的默认用户（REST 路径从 JWT 获取）
        self.default_user_id: str = "rca-agent"
        self.default_user_role: str = "quality_manager"

    # ── AsyncA2AServer 覆写 ──

    @property
    def graph(self):
        return self._graph

    @property
    def checkpointer(self):
        return self._checkpointer

    async def build_input(self, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        """A2A 载荷 → LangGraph 初始状态。"""
        req = AnalysisRequest(**payload)
        trace_id = new_trace_id()
        set_trace_id(trace_id)

        memory_context = await self._memory.build_planner_context(
            session_id=session_id,
            user_id=payload.get("user_id", self.default_user_id),
            query=req.user_query,
            defect_type=req.defect_type,
        )
        if req.batch_id:
            memory_context = f"【目标批次】{req.batch_id}\n\n{memory_context}"

        return {
            "trace_id": trace_id,
            "session_id": session_id,
            "user_id": payload.get("user_id", self.default_user_id),
            "user_role": payload.get("user_role", self.default_user_role),
            "user_query": req.user_query,
            "batch_id": req.batch_id or "",
            "defect_type": req.defect_type or "",
            "memory_context": memory_context,
            "prior_tool_calls": prior_tool_records(req.prior_tool_calls),
            "evidence": prior_evidence_items(req.prior_evidence),
            "tool_calls": prior_tool_records(req.prior_tool_calls),
        }

    async def extract_output(self, graph_state: dict[str, Any]) -> dict[str, Any]:
        """LangGraph 最终状态 → AnalysisResponse 格式。"""
        evidence_raw = graph_state.get("evidence", []) or []
        evidence_out = [
            {
                "description": ev.get("description", ""),
                "source_tool": ev.get("source_tool", ""),
                "data_ref": ev.get("data_ref", ""),
                "confidence": ev.get("confidence", 0.0),
            }
            for ev in evidence_raw
        ]
        trace_id = graph_state.get("trace_id", graph_state.get("session_id", ""))
        thread_id = graph_state.get("session_id", trace_id)
        requires_hitl = graph_state.get("requires_hitl", False)
        return {
            "trace_id": trace_id,
            "thread_id": thread_id,
            "status": "hitl" if requires_hitl else "done",
            "root_cause": graph_state.get("root_cause", ""),
            "recommendations": graph_state.get("recommendations", []),
            "confidence": graph_state.get("confidence", 0.0),
            "report_md": graph_state.get("final_report", ""),
            "requires_hitl": requires_hitl,
            "evidence": evidence_out,
            "rca_artifacts": graph_state.get("rca_artifacts"),
        }

    async def handle_task(self, task: Task) -> Task:
        """覆写 handle_task：LangGraph __interrupt__ 检测（非异常）+ 超时 + memory.persist。"""
        payload = self.payload_from_task(task)
        session_id = str(task.session_id or task.id)
        thread_id = await self._athread_id_for(session_id)

        try:
            graph_input = await self.build_input(payload, session_id)
        except Exception as exc:
            logger.exception("rca.build_input_failed", session_id=session_id, error=str(exc))
            return self.complete_task(task, {"error": str(exc)}, state=TaskState.FAILED)

        config = {"configurable": {"thread_id": thread_id}}
        callbacks = build_langsmith_callbacks()
        invoke_config = {**config, "recursion_limit": 50}
        if callbacks:
            invoke_config["callbacks"] = callbacks

        try:
            result = await asyncio.wait_for(
                self.graph.ainvoke(graph_input, config=invoke_config),
                timeout=300.0,
            )
        except asyncio.TimeoutError:
            logger.error("rca.graph_timeout", session_id=session_id, thread_id=thread_id)
            return self.complete_task(
                task,
                {
                    "trace_id": session_id,
                    "thread_id": thread_id,
                    "status": "failed",
                    "root_cause": "",
                    "recommendations": [],
                    "confidence": 0.0,
                    "report_md": "RCA analysis timed out after 5 minutes",
                    "requires_hitl": False,
                    "evidence": [],
                },
                state=TaskState.FAILED,
            )
        except Exception as exc:
            # LangGraph 抛出异常（非 __interrupt__ 路径，兜底）
            logger.exception("rca.graph_invoke_failed", session_id=session_id, error=str(exc))
            return self.complete_task(task, {"error": str(exc)}, state=TaskState.FAILED)

        # ---- __interrupt__ 检测（RCA LangGraph 版本用返回值，非异常） ----
        interrupts = (result or {}).get("__interrupt__")
        if interrupts:
            logger.info("rca.graph_interrupted", session_id=session_id, thread_id=thread_id)
            interrupt_value = getattr(interrupts[0], "value", str(interrupts[0])) if interrupts else ""
            task.artifacts = [
                {
                    "parts": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "thread_id": thread_id,
                                    "session_id": session_id,
                                    "interrupt_value": str(interrupt_value),
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
            task.status = TaskStatus(state=A2ATaskState.INPUT_REQUIRED)
            return task

        # ---- 正常完成 ----
        try:
            await self._memory.persist_analysis(
                thread_id,
                graph_input.get("user_id", "rca-agent"),
                graph_input.get("user_query", ""),
                result,
            )
        except Exception as exc:
            logger.warning("rca.memory_persist_failed", session_id=session_id, error=str(exc))

        output = await self.extract_output(result)
        task = self.complete_task(task, output, state=TaskState.COMPLETED)
        await self._aclear_thread(session_id)
        return task

    async def handle_resume(
        self,
        task_id: str,
        thread_id: str,
        feedback: dict[str, Any],
        _fwd: dict[str, str],
    ) -> tuple[dict[str, Any], str]:
        """覆写 handle_resume：__interrupt__ 检测 + 多层 HITL。"""
        config = {"configurable": {"thread_id": thread_id}}
        callbacks = build_langsmith_callbacks()
        invoke_config = {**config, "recursion_limit": 50}
        if callbacks:
            invoke_config["callbacks"] = callbacks

        try:
            result = await asyncio.wait_for(
                self.graph.ainvoke(
                    LangGraphCommand(resume=feedback),
                    config=invoke_config,
                ),
                timeout=300.0,
            )
        except asyncio.TimeoutError:
            return {"status": "failed", "error": "HITL resume timed out"}, TaskState.FAILED
        except Exception as exc:
            logger.exception("rca.resume_failed", thread_id=thread_id, error=str(exc))
            return {"error": str(exc)}, TaskState.FAILED

        # 检测多层 HITL（resume 后又遇到 interrupt）
        interrupts = (result or {}).get("__interrupt__")
        if interrupts:
            logger.info("rca.resume_re_interrupted", thread_id=thread_id, session_id=task_id)
            interrupt_value = getattr(interrupts[0], "value", str(interrupts[0])) if interrupts else ""
            task_obj = Task(id=task_id, session_id=task_id)
            task_obj.artifacts = [
                {
                    "parts": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "thread_id": thread_id,
                                    "session_id": task_id,
                                    "interrupt_value": str(interrupt_value),
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
            task_obj.status = TaskStatus(state=A2ATaskState.INPUT_REQUIRED)
            return self._task_result(task_obj), TaskState.INPUT_REQUIRED

        output = await self.extract_output(result)
        await self._aclear_thread(task_id)
        return output, TaskState.COMPLETED


# ── 启动 / 关闭 ──────────────────────────────────────────────────────


class AppServices:
    registry: ToolRegistry
    mcp_clients: list
    memory: MemoryHarness
    server: RcaAgentServer
    mcp_connected: list[str]
    mcp_failed: list[str]
    checkpoint_backend: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    registry = ToolRegistry(permission_checker=PermissionChecker())
    mcp_clients, connected, failed = await bootstrap_registry(registry)
    memory = await MemoryHarness.create()
    await FMEARegistry.load()
    graph = build_quality_analysis_graph(registry, checkpointer=build_checkpointer())

    redis_url = getattr(settings, "redis_url", None) or None

    server = RcaAgentServer(
        RCA_AGENT_CARD,
        memory,
        registry,
        graph,
        checkpointer=build_checkpointer(),
        redis_url=redis_url,
    )

    svc = AppServices()
    svc.registry = registry
    svc.mcp_clients = mcp_clients
    svc.memory = memory
    svc.server = server
    svc.mcp_connected = connected
    svc.mcp_failed = failed
    svc.checkpoint_backend = settings.langgraph_checkpoint_backend
    app.state.svc = svc
    server.mount(app)

    # 统一可观测性初始化（OTel 主 tracing + LangSmith 可选）
    from harness_core.observability import init_observability, instrument_fastapi
    _otel_ok = init_observability(
        service_name="quality-rca-agent",
        otel_endpoint=getattr(settings, "otel_exporter_otlp_endpoint", None),
        langsmith_api_key=getattr(settings, "langsmith_api_key", None),
        langsmith_project=getattr(settings, "langsmith_project", "battery-agent"),
    )
    if _otel_ok:
        instrument_fastapi(app)

    # 注册到 Capability Registry
    await register_with_registry(
        registry_url=settings.registry_url,
        agent_name="quality-rca-agent",
        agent_description=RCA_AGENT_CARD.description,
        agent_url=f"http://localhost:{settings.api_port}",
        capabilities=RCA_AGENT_CARD.capabilities,
    )

    logger.info(
        "rca.startup",
        mcp_connected=connected,
        mcp_failed=failed,
        checkpoint_backend=svc.checkpoint_backend,
    )

    try:
        yield
    finally:
        for c in mcp_clients:
            await c.close()


app = FastAPI(title="quality-rca-agent", version="0.2.0", lifespan=lifespan)

bearer = HTTPBearer()


def _principal_from_headers(fwd: dict[str, str]) -> TokenPayload:
    auth_hdr = fwd.get("authorization") or fwd.get("Authorization") or ""
    token = auth_hdr.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Authorization required")
    try:
        return decode_token(token)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc


# ── 健康检查 ─────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    svc = app.state.svc
    status = "ok" if not svc.mcp_failed else "degraded"
    return {
        "status": status,
        "agent": "quality-rca-agent",
        "mcp_connected": svc.mcp_connected,
        "mcp_failed": svc.mcp_failed,
        "checkpoint_backend": svc.checkpoint_backend,
    }


# ── REST 兼容端点（保留，非 A2A 路径仍可用）────────────────────────


def auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> TokenPayload:
    try:
        return decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc


@app.post("/v1/analysis/quality", response_model=AnalysisResponse)
async def quality_analysis(
    req: AnalysisRequest,
    principal: TokenPayload = Depends(auth),
    x_trace_id: str | None = Header(default=None, alias="X-Trace-Id"),
) -> AnalysisResponse:
    """REST 兼容入口；标准 A2A 请用 POST /a2a/v1/tasks/send。"""
    svc = app.state.svc
    payload = req.model_dump(mode="json")
    payload["user_id"] = principal.sub
    payload["user_role"] = principal.role

    task = Task(id=req.session_id or new_trace_id(), session_id=req.session_id or new_trace_id())
    out_task = await svc.server.handle_task(task)

    for art in out_task.artifacts or []:
        for part in art.get("parts", []):
            if part.get("type") == "text" and part.get("text"):
                import json
                try:
                    return AnalysisResponse(**json.loads(part["text"]))
                except Exception:
                    pass
    raise HTTPException(500, "RCA analysis failed")


@app.post("/v1/hitl/resolve", response_model=AnalysisResponse)
async def hitl_resolve(req: HITLResolveRequest, principal: TokenPayload = Depends(auth)):
    """HITL 签核 REST 入口（兼容旧路径）。"""
    svc = app.state.svc
    feedback = {
        "approved": req.approved,
        "feedback": req.feedback,
        "reviewer_id": principal.sub,
    }
    if req.root_cause:
        feedback["root_cause"] = req.root_cause
    if req.recommendations:
        feedback["recommendations"] = req.recommendations
    if req.extra:
        feedback.update(req.extra)

    tid = req.thread_id or new_trace_id()
    result, state = await svc.server.handle_resume(
        task_id=tid,
        thread_id=req.thread_id or tid,
        feedback=feedback,
        _fwd={},
    )
    if state in (TaskState.INPUT_REQUIRED, TaskState.FAILED):
        raise HTTPException(400 if state == TaskState.INPUT_REQUIRED else 500, str(result.get("error", "")))
    return AnalysisResponse(**result)
