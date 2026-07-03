"""Playbook Orchestrator：剧本编排 + PlatformContext + SSE（无 LLM，非 Agent）。"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from harness_core.audit.tracer import new_trace_id, set_trace_id
from harness_core.context.session_store import SessionStore, build_session_store
from harness_core.events.bus import get_event_bus, init_event_bus
from harness_core.events.stream import publish_task_event, sse_event_stream
from harness_core.playbook_engine import PlaybookEngine
from platform_contracts.a2a import A2AClient, A2AError, mount_a2a
from platform_contracts.agent_handoffs import RcaInvokeRequest, Report8dRequest
from platform_contracts.agent_network import AgentNetwork
from platform_contracts.agent_registry_seed import ALL_REGISTERED_AGENT_CARDS, ORCHESTRATOR_CARD
from platform_contracts.platform_context import PlatformContext
from platform_contracts.triage_stub import resolve_triage
from platform_contracts.task_events import TaskEventType
from platform_contracts.task_state import TaskState

logger = structlog.get_logger(__name__)


class OrchestratorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    triage_agent_url: str = "http://127.0.0.1:8001"
    trace_worker_url: str = "http://127.0.0.1:8002"
    rca_agent_url: str = "http://127.0.0.1:8003"
    report_8d_worker_url: str = "http://127.0.0.1:8004"
    report_reporter_agent_url: str = "http://127.0.0.1:8004"
    registry_url: str = "http://127.0.0.1:8021"
    http_timeout: float = 120.0
    http_retries: int = 2
    internal_service_key: str = "dev-router-key"
    sse_heartbeat_interval: float = 15.0
    context_backend: Literal["memory", "redis", "postgres"] = "memory"
    event_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    postgres_dsn: str = "postgresql://battery:battery@localhost:5432/battery_agent"
    context_ttl_seconds: int = 86_400


settings = OrchestratorSettings()


def _agent_network() -> AgentNetwork:
    return AgentNetwork.from_cards(
        ALL_REGISTERED_AGENT_CARDS,
        url_overrides={
            "triage-agent": settings.triage_agent_url,
            "trace-worker": settings.trace_worker_url,
            "quality-rca-agent": settings.rca_agent_url,
            "report-8d-worker": settings.report_8d_worker_url,
            "report-reporter-agent": settings.report_reporter_agent_url,
        },
    )
_sessions: SessionStore = build_session_store(
    settings.context_backend,
    redis_url=settings.redis_url,
    ttl_seconds=settings.context_ttl_seconds,
    postgres_dsn=settings.postgres_dsn,
)

# Playbook DSL 引擎（替代硬编码 if-else）
_playbook_engine: PlaybookEngine | None = None


class DispatchRequest(BaseModel):
    session_id: str | None = None
    trace_id: str | None = None
    playbook: Literal["investigate", "trace_only", "rca", "close_loop"] = "investigate"
    message: str = ""
    batch_id: str | None = None
    factory_id: str | None = None
    defect_type: str | None = None
    skip_triage: bool = False
    confirm_rca: bool = True
    hitl_approved: bool = Field(default=False, description="close_loop / investigate 续跑时 HITL 已批准")
    async_mode: bool = Field(default=False, description="true 时后台执行并通过 SSE 推送")
    authorization: str | None = None


class DispatchResponse(BaseModel):
    session_id: str
    trace_id: str
    playbook: str
    task_status: str
    current_step: str
    context: PlatformContext
    rca_result: dict[str, Any] | None = None
    report_8d_result: dict[str, Any] | None = None


class AsyncDispatchResponse(BaseModel):
    session_id: str
    trace_id: str
    playbook: str
    task_status: str = TaskState.SUBMITTED
    sse_url: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _playbook_engine
    init_event_bus(settings.event_backend, redis_url=settings.redis_url)
    _playbook_engine = PlaybookEngine(Path(__file__).parent.parent.parent / "config" / "playbooks.yaml")
    _playbook_engine.load()
    logger.info(
        "router.startup",
        context_backend=settings.context_backend,
        event_backend=settings.event_backend,
        playbooks_loaded=len(_playbook_engine.playbooks),
        redis_url=settings.redis_url
        if settings.context_backend == "redis" or settings.event_backend == "redis"
        else None,
    )
    yield


app = FastAPI(title="playbook-orchestrator", version="0.4.0", lifespan=lifespan)


def _session_id(req: DispatchRequest) -> str:
    return req.session_id or f"sess_{uuid.uuid4().hex[:12]}"


async def _get_or_create_context(req: DispatchRequest) -> PlatformContext:
    sid = _session_id(req)
    ctx = await _sessions.get(sid)
    if ctx is None:
        ctx = PlatformContext(
            session_id=sid,
            trace_id=req.trace_id or new_trace_id(),
            factory_id=req.factory_id,
            batch_id=req.batch_id,
            defect_type=req.defect_type,
        )
    if req.trace_id:
        ctx.trace_id = req.trace_id
    if req.batch_id:
        ctx.batch_id = req.batch_id
    if req.defect_type:
        ctx.defect_type = req.defect_type
    await _sessions.save(ctx)
    return ctx


async def _save_context(ctx: PlatformContext) -> None:
    await _sessions.save(ctx)


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    path: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(1, settings.http_retries + 2):
        try:
            resp = await client.post(f"{url}{path}", json=body, headers=headers or {})
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"upstream 5xx: {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            if resp.status_code >= 400:
                raise HTTPException(resp.status_code, resp.text)
            return resp.json()
        except HTTPException:
            raise
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_err = exc
            logger.warning("router.http_retry", url=url, path=path, attempt=attempt, error=str(exc))
    raise HTTPException(502, f"upstream failed: {url}{path}: {last_err}") from last_err


async def _a2a_send(
    delegator: A2AClient,
    agent_url: str,
    payload: dict[str, Any],
    *,
    session_id: str,
    schema: str,
) -> dict[str, Any]:
    try:
        return await delegator.send(
            agent_url,
            payload,
            session_id=session_id,
            schema=schema,
        )
    except A2AError as exc:
        raise HTTPException(502, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc


async def _emit_step(
    session_id: str,
    trace_id: str,
    *,
    step: str,
    agent: str,
    message: str,
) -> None:
    await publish_task_event(
        get_event_bus(),
        session_id=session_id,
        trace_id=trace_id,
        event_type=TaskEventType.STEP_STARTED,
        task_status=TaskState.RUNNING,
        step=step,
        agent=agent,
        message=message,
    )


async def _apply_triage(
    delegator: A2AClient,
    ctx: PlatformContext,
    req: DispatchRequest,
    session_id: str,
    trace_id: str,
) -> None:
    """分诊：优先调用 triage-agent（A2A），失败降级到内联 stub。"""
    ctx.current_step = "triage"
    await _emit_step(session_id, trace_id, step="triage", agent="triage-agent", message="异常分诊与意图识别")

    tri = None
    try:
        tri_raw = await _a2a_send(
            delegator,
            _agent_network().base_url("triage-agent"),
            {
                "session_id": session_id,
                "query": req.message or "",
                "batch_id": ctx.batch_id,
            },
            session_id=session_id,
            schema="TriageRequest",
        )
        tri = TriageResponse(**tri_raw)
    except Exception as exc:
        logger.warning("triage.agent_failed", session_id=session_id, error=str(exc))

    if tri is None or getattr(tri, "stub", True):
        tri = resolve_triage(
            query=req.message,
            batch_id=ctx.batch_id,
            defect_type=req.defect_type or ctx.defect_type,
            session_id=ctx.session_id,
        )
        agent_name = "triage-stub"
        logger.info("triage.rule_fallback", session_id=session_id)
    else:
        agent_name = "triage-agent"

    ctx.defect_type = tri.defect_type
    ctx.severity = tri.severity
    ctx.triage_result = tri.model_dump(mode="json")
    await _save_context(ctx)
    await publish_task_event(
        get_event_bus(),
        session_id=session_id,
        trace_id=trace_id,
        event_type=TaskEventType.STEP_COMPLETED,
        task_status=TaskState.RUNNING,
        step="triage",
        agent=agent_name,
        payload={"defect_type": ctx.defect_type, "severity": ctx.severity, "stub": getattr(tri, "stub", True)},
    )


async def _run_trace(
    delegator: A2AClient,
    ctx: PlatformContext,
    req: DispatchRequest,
    session_id: str,
    trace_id: str,
) -> None:
    ctx.current_step = "trace"
    await _emit_step(session_id, trace_id, step="trace", agent="trace-worker", message="批次追溯")
    tr = await _a2a_send(
        delegator,
        _agent_network().base_url("trace-worker"),
        {"session_id": ctx.session_id, "batch_id": ctx.batch_id, "query": req.message},
        session_id=ctx.session_id,
        schema="TraceRequest",
    )
    ctx.prior_evidence.extend(tr.get("evidence", []))
    ctx.prior_tool_calls.extend(tr.get("tool_calls", []))
    await _save_context(ctx)
    await publish_task_event(
        get_event_bus(),
        session_id=session_id,
        trace_id=trace_id,
        event_type=TaskEventType.STEP_COMPLETED,
        task_status=TaskState.RUNNING,
        step="trace",
        agent="trace-worker",
        payload={"evidence_count": len(tr.get("evidence", []))},
    )


def _fallback_rca(ctx: PlatformContext) -> dict[str, Any]:
    """RCA 兜底：基于 trace evidence + FMEA 规则构造 root_cause。
    置信度固定 0.3，强制走 HITL。"""
    defect = ctx.defect_type or "未知缺陷"
    evidence_summary = "; ".join(
        e.get("description", "") for e in (ctx.prior_evidence or [])[:3]
    ) or "无可追溯证据"

    root_cause = (
        f"兜底判定：{defect}（证据链：{evidence_summary}）。"
        f"RCA Agent 未能产出有效根因，基于 prior_evidence 与 FMEA 规则构造初判。"
        f"该结论置信度低，必须人工审核。"
    )
    logger.warning("rca.fallback", session_id=ctx.session_id, defect=defect)
    return {
        "thread_id": ctx.session_id,
        "status": "hitl",
        "root_cause": root_cause,
        "confidence": 0.3,
        "requires_hitl": True,
        "recommendations": [
            f"人工复查 {defect} 相关工序参数与设备状态",
            "核对同批次在制品的检测结果",
            "确认 FMEA 因果树是否需要新增缺陷条目",
        ],
        "evidence": list(ctx.prior_evidence),
        "rca_artifacts": {"fallback": True, "source": "orchestrator._fallback_rca"},
    }


async def _run_rca(
    delegator: A2AClient,
    ctx: PlatformContext,
    req: DispatchRequest,
    session_id: str,
    trace_id: str,
) -> dict[str, Any]:
    ctx.current_step = "rca"
    await _emit_step(session_id, trace_id, step="rca", agent="quality-rca-agent", message="根因分析")
    rca_body = RcaInvokeRequest(
        session_id=ctx.session_id,
        user_query=req.message or "质量异常根因分析",
        batch_id=ctx.batch_id,
        defect_type=ctx.defect_type,
        prior_tool_calls=ctx.prior_tool_calls,
        prior_evidence=ctx.prior_evidence,
    )
    rca_url = _agent_network().base_url("quality-rca-agent")

    rca = None
    try:
        rca = await _a2a_send(
            delegator, rca_url, rca_body.model_dump(),
            session_id=ctx.session_id, schema="RcaInvokeRequest",
        )
        # 校验 RCA 产出：root_cause 为空或 confidence 为 0 → 视为无效
        if not rca.get("root_cause") or not rca.get("confidence"):
            logger.warning("rca.empty_result", session_id=session_id)
            rca = None
    except Exception as exc:
        logger.warning("rca.call_failed", session_id=session_id, error=str(exc))

    if rca is None:
        rca = _fallback_rca(ctx)

    ctx.rca.thread_id = rca.get("thread_id")
    ctx.rca.status = "hitl" if rca.get("requires_hitl") else "done"
    ctx.rca.root_cause = rca.get("root_cause")
    ctx.rca.confidence = rca.get("confidence")
    ctx.rca.recommendations = list(rca.get("recommendations") or [])
    rca_evidence = rca.get("evidence") or []
    if rca_evidence:
        ctx.rca.evidence = list(rca_evidence)
    else:
        ctx.rca.evidence = list(ctx.prior_evidence)
    ctx.artifacts["quality-rca-agent"] = rca  # 通用扩展槽
    ctx.task_status = rca.get("status", "done")
    ctx.current_step = "rca"
    await _save_context(ctx)
    return rca


async def _run_report_8d(
    delegator: A2AClient,
    ctx: PlatformContext,
    session_id: str,
    trace_id: str,
    *,
    hitl_approved: bool,
) -> dict[str, Any]:
    ctx.current_step = "report_8d"
    agent_name = "report-reporter-agent"
    await _emit_step(session_id, trace_id, step="report_8d", agent=agent_name, message="8D 定稿")
    body = Report8dRequest(
        session_id=ctx.session_id,
        root_cause=ctx.rca.root_cause or "",
        evidence=ctx.rca.evidence or ctx.prior_evidence,
        hitl_approved=hitl_approved,
        confidence=ctx.rca.confidence,
        defect_type=ctx.defect_type,
        factory_id=ctx.factory_id,
        recommendations=ctx.rca.recommendations or [],
        rca_artifacts=ctx.rca.rca_artifacts,
    )
    rep = await _a2a_send(
        delegator,
        _agent_network().base_url(agent_name),
        body.model_dump(mode="json"),
        session_id=ctx.session_id,
        schema="Report8dRequest",
    )
    ctx.report_8d.report_md = rep.get("report_md")
    ctx.report_8d.capa_id = rep.get("capa_id")
    ctx.report_8d.qms_status = rep.get("qms_status")
    ctx.report_8d.generation_mode = rep.get("generation_mode")
    ctx.task_status = "done"
    ctx.current_step = "report_8d"
    await _save_context(ctx)
    await publish_task_event(
        get_event_bus(),
        session_id=session_id,
        trace_id=trace_id,
        event_type=TaskEventType.STEP_COMPLETED,
        task_status=TaskState.RUNNING,
        step="report_8d",
        agent=agent_name,
        payload={"capa_id": ctx.report_8d.capa_id, "qms_status": ctx.report_8d.qms_status, "generation_mode": ctx.report_8d.generation_mode},
    )
    return rep


async def _run_playbook(
    ctx: PlatformContext,
    req: DispatchRequest,
    trace_id: str,
    headers: dict[str, str],
) -> DispatchResponse:
    """Playbook DSL 引擎驱动（替代硬编码 if-else）。"""
    bus = get_event_bus()
    session_id = ctx.session_id

    await publish_task_event(
        bus,
        session_id=session_id,
        trace_id=trace_id,
        event_type=TaskEventType.RUNNING,
        task_status=TaskState.RUNNING,
        message=f"playbook={req.playbook}",
    )

    engine = _playbook_engine
    if not engine or not engine._loaded:
        raise HTTPException(500, "PlaybookEngine not loaded")

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        delegator = A2AClient(client, headers=headers)

        # ---- call_step 回调：将 engine 的"调 agent"映射到实际 A2A 调用 ----
        async def _call_step(agent_name: str, step_def: dict, engine_ctx: dict) -> dict:
            if agent_name in ("triage-stub", "triage-agent"):
                await _apply_triage(delegator, ctx, req, session_id, trace_id)
                return {
                    "defect_type": ctx.defect_type or "",
                    "severity": ctx.severity or "medium",
                }

            if agent_name == "trace-worker":
                await _run_trace(delegator, ctx, req, session_id, trace_id)
                return {
                    "evidence": list(ctx.prior_evidence),
                    "tool_calls": list(ctx.prior_tool_calls),
                }

            if agent_name == "quality-rca-agent":
                return await _run_rca(delegator, ctx, req, session_id, trace_id)

            if agent_name == "report-reporter-agent":
                hitl_approved = req.hitl_approved or bool(
                    engine_ctx.get("hitl_approved", False)
                )
                return await _run_report_8d(
                    delegator, ctx, session_id, trace_id, hitl_approved=hitl_approved,
                )

            raise ValueError(f"Unknown agent: {agent_name}")

        # ---- emit_event 回调：将引擎事件转为 SSE 推送 ----
        async def _emit_event(event_type: str, step: str, agent: str, message: str) -> None:
            type_map = {
                "step_started": TaskEventType.STEP_STARTED,
                "step_completed": TaskEventType.STEP_COMPLETED,
                "input_required": TaskEventType.INPUT_REQUIRED,
                "hitl": TaskEventType.INPUT_REQUIRED,
                "failed": TaskEventType.FAILED,
            }
            status_map = {
                "step_started": TaskState.RUNNING,
                "step_completed": TaskState.RUNNING,
                "input_required": TaskState.INPUT_REQUIRED,
                "hitl": TaskState.INPUT_REQUIRED,
                "failed": TaskState.FAILED,
            }
            await publish_task_event(
                bus,
                session_id=session_id,
                trace_id=trace_id,
                event_type=type_map.get(event_type, TaskEventType.STEP_STARTED),
                task_status=status_map.get(event_type, TaskState.RUNNING),
                step=step,
                agent=agent,
                message=message,
            )

        # ---- 执行引擎 ----
        ctx_dict = ctx.model_dump(mode="json")
        req_dict = req.model_dump(mode="json")

        engine_result = await engine.execute(
            playbook=req.playbook,
            ctx=ctx_dict,
            req=req_dict,
            trace_id=trace_id,
            session_id=session_id,
            call_step=_call_step,
            emit_event=_emit_event,
        )

        # ---- 将引擎结果映射回 DispatchResponse ----
        status = engine_result.get("status", "failed")
        if status == "failed":
            raise HTTPException(500, engine_result.get("error", "playbook failed"))

        # 中间状态：input_required / hitl → 返回上下文，等续跑
        if status in ("awaiting_confirm", "hitl"):
            ctx.task_status = (
                "awaiting_confirm" if status == "awaiting_confirm" else "hitl"
            )
            ctx.current_step = engine_result.get("current_step", "")
            await _save_context(ctx)

            payload = {}
            hitl_req = engine_result.get("hitl_request", {})
            if hitl_req:
                payload = {
                    "step": hitl_req.get("step"),
                    "reason": engine_result.get("input_required", {}).get("message"),
                }
            await publish_task_event(
                bus,
                session_id=session_id,
                trace_id=trace_id,
                event_type=TaskEventType.INPUT_REQUIRED,
                task_status=TaskState.INPUT_REQUIRED,
                step=ctx.current_step,
                message=engine_result.get("error", ""),
                payload=payload,
            )
            rca = ctx_dict.get("rca", {})
            return DispatchResponse(
                session_id=ctx.session_id,
                trace_id=trace_id,
                playbook=req.playbook,
                task_status=ctx.task_status,
                current_step=ctx.current_step,
                context=ctx,
                rca_result=rca if rca.get("root_cause") else None,
            )

        # 完成态
        ctx.task_status = "done"
        await _save_context(ctx)
        await publish_task_event(
            bus,
            session_id=session_id,
            trace_id=trace_id,
            event_type=TaskEventType.COMPLETED,
            task_status=TaskState.COMPLETED,
            step=ctx.current_step or "done",
            payload={
                "root_cause": ctx.rca.root_cause,
                "capa_id": ctx.report_8d.capa_id,
            },
        )

        return DispatchResponse(
            session_id=ctx.session_id,
            trace_id=trace_id,
            playbook=req.playbook,
            task_status="done",
            current_step="done",
            context=ctx,
            rca_result=ctx_dict.get("rca"),
            report_8d_result=ctx_dict.get("report_8d"),
        )


async def _background_dispatch(
    ctx: PlatformContext, req: DispatchRequest, trace_id: str, headers: dict[str, str]
) -> None:
    try:
        await _run_playbook(ctx, req, trace_id, headers)
    except Exception as exc:
        logger.exception("router.background_dispatch_failed", session_id=ctx.session_id, error=str(exc))
    finally:
        await get_event_bus().close_session(ctx.session_id)


def _to_a2a_state(task_status: str) -> str:
    mapping = {
        "completed": TaskState.COMPLETED,
        "done": TaskState.COMPLETED,
        "awaiting_confirm": TaskState.INPUT_REQUIRED,
        "hitl": TaskState.INPUT_REQUIRED,
        "failed": TaskState.FAILED,
    }
    return mapping.get(task_status, TaskState.RUNNING)


async def execute_dispatch(
    req: DispatchRequest,
    fwd: dict[str, str],
) -> tuple[dict[str, Any], str]:
    """Playbook 编排核心；REST 与 A2A tasks/send 共用。"""
    ctx = await _get_or_create_context(req)
    trace_id = ctx.trace_id or new_trace_id()
    set_trace_id(trace_id)
    ctx.trace_id = trace_id
    headers: dict[str, str] = {"X-Trace-Id": trace_id}
    auth = fwd.get("authorization") or req.authorization
    if auth:
        headers["Authorization"] = auth
    else:
        headers["Authorization"] = f"Bearer {settings.internal_service_key}"

    await publish_task_event(
        get_event_bus(),
        session_id=ctx.session_id,
        trace_id=trace_id,
        event_type=TaskEventType.SUBMITTED,
        task_status=TaskState.SUBMITTED,
        message=req.message,
        payload={"playbook": req.playbook, "batch_id": req.batch_id},
    )

    if req.async_mode:
        asyncio.create_task(_background_dispatch(ctx, req, trace_id, headers))
        return (
            AsyncDispatchResponse(
                session_id=ctx.session_id,
                trace_id=trace_id,
                playbook=req.playbook,
                sse_url=f"/a2a/v1/sessions/{ctx.session_id}/events",
            ).model_dump(),
            TaskState.SUBMITTED,
        )

    resp = await _run_playbook(ctx, req, trace_id, headers)
    return resp.model_dump(mode="json"), _to_a2a_state(resp.task_status)


async def _router_a2a_handler(
    payload: dict[str, Any],
    schema: str,
    fwd: dict[str, str],
) -> tuple[dict[str, Any], str]:
    req = DispatchRequest(**payload)
    return await execute_dispatch(req, fwd)


mount_a2a(app, ORCHESTRATOR_CARD, _router_a2a_handler)


@app.get("/health")
async def health():
    probes = {
        "trace_worker": _agent_network().base_url("trace-worker"),
        "rca_agent": _agent_network().base_url("quality-rca-agent"),
        "report_8d_worker": _agent_network().base_url("report-8d-worker"),
        "registry": settings.registry_url,
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        status = {}
        for name, base in probes.items():
            try:
                r = await client.get(f"{base}/health")
                status[name] = "ok" if r.status_code == 200 else "degraded"
            except Exception:
                status[name] = "down"
    overall = "ok" if all(v == "ok" for v in status.values()) else "degraded"
    return {
        "status": overall,
        "service": "router",
        "context_backend": settings.context_backend,
        "event_backend": settings.event_backend,
        "dependencies": status,
    }


@app.post("/a2a/v1/router/dispatch")
async def dispatch(req: DispatchRequest) -> DispatchResponse | AsyncDispatchResponse:
    """REST 兼容入口；标准 A2A 请用 POST /a2a/v1/tasks/send。"""
    fwd = {"authorization": req.authorization} if req.authorization else {}
    result, _ = await execute_dispatch(req, fwd)
    if req.async_mode:
        return AsyncDispatchResponse(**result)
    return DispatchResponse(**result)


@app.get("/a2a/v1/sessions/{session_id}/events")
async def stream_session_events(
    session_id: str,
    after_seq: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    if last_event_id and last_event_id.isdigit():
        after_seq = max(after_seq, int(last_event_id))
    if not await _sessions.exists(session_id) and after_seq == 0:
        raise HTTPException(404, "session not found")
    return StreamingResponse(
        sse_event_stream(session_id, after_seq=after_seq, heartbeat_interval=settings.sse_heartbeat_interval),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/a2a/v1/context/{session_id}")
async def get_context(session_id: str) -> PlatformContext:
    ctx = await _sessions.get(session_id)
    if not ctx:
        raise HTTPException(404, "session not found")
    return ctx
