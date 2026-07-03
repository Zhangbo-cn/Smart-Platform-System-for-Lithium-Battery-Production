"""Client Gateway：门户 + SSE + HITL 签核回写（无 LLM，非 Agent）。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from platform_contracts.a2a import A2AClient, A2AError, mount_a2a
from platform_contracts.agent_registry_seed import CLIENT_GATEWAY_CARD
from platform_contracts.plan_result import PlannerRequest
from platform_contracts.task_state import TaskState

logger = structlog.get_logger(__name__)
STATIC_DIR = Path(__file__).parent / "static"

_PLAYBOOKS_NEED_BATCH = frozenset({"investigate", "trace_only", "close_loop"})


class ClientSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    orchestrator_url: str = "http://127.0.0.1:8020"
    planner_url: str = "http://127.0.0.1:8011"
    rca_agent_url: str = "http://127.0.0.1:8003"
    http_timeout: float = 120.0
    internal_service_key: str = "dev-router-key"
    auto_plan: bool = True


settings = ClientSettings()
app = FastAPI(title="client-gateway", version="0.3.0")


class AssistantTaskRequest(BaseModel):
    message: str
    playbook: Literal["investigate", "trace_only", "rca", "close_loop"] | None = None
    batch_id: str | None = None
    factory_id: str | None = None
    defect_type: str | None = None
    skip_triage: bool = False
    confirm_rca: bool = True
    hitl_approved: bool = False
    session_id: str | None = None
    authorization: str | None = None
    skip_planner: bool = False


class HitlResumeRequest(BaseModel):
    approved: bool = True
    root_cause: str | None = None
    feedback: str = ""
    playbook: Literal["close_loop", "investigate", "rca"] = "close_loop"
    thread_id: str | None = None
    hitl_request_id: str | None = None


class AssistantTaskResponse(BaseModel):
    task_id: str
    session_id: str
    trace_id: str
    status: str
    sse_url: str
    poll_url: str
    ui_url: str = "/"
    planned_playbook: str | None = None


class ResumeResponse(BaseModel):
    session_id: str
    status: str
    message: str
    context: dict[str, Any] | None = None


def _service_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.internal_service_key}"}


def _merge_auth_headers(
    fwd: dict[str, str] | None = None,
    authorization: str | None = None,
) -> dict[str, str]:
    headers = _service_headers()
    if fwd and fwd.get("authorization"):
        headers["Authorization"] = fwd["authorization"]
    elif authorization:
        headers["Authorization"] = authorization
    return headers


def should_invoke_planner(req: AssistantTaskRequest) -> bool:
    if req.skip_planner or not settings.auto_plan:
        return False
    if not req.playbook:
        return True
    if req.playbook in _PLAYBOOKS_NEED_BATCH and not req.batch_id:
        return True
    return False


async def _plan_task(
    client: httpx.AsyncClient,
    req: AssistantTaskRequest,
    headers: dict[str, str],
) -> dict[str, Any]:
    delegator = A2AClient(client, headers=headers)
    plan_req = PlannerRequest(
        message=req.message,
        playbook=req.playbook,
        batch_id=req.batch_id,
        factory_id=req.factory_id,
        defect_type=req.defect_type,
        session_id=req.session_id,
    )
    try:
        return await delegator.send(
            settings.planner_url,
            plan_req.model_dump(exclude_none=True),
            session_id=req.session_id or f"sess_{uuid.uuid4().hex[:12]}",
            schema="PlannerRequest",
        )
    except A2AError as exc:
        raise HTTPException(502, f"planner failed: {exc}") from exc


async def _dispatch_to_orchestrator(
    client: httpx.AsyncClient,
    body: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    session_id = body.get("session_id") or f"sess_{uuid.uuid4().hex[:12]}"
    payload = {**body, "session_id": session_id, "async_mode": body.get("async_mode", True)}
    delegator = A2AClient(client, headers=headers)
    try:
        return await delegator.send(
            settings.orchestrator_url,
            payload,
            session_id=session_id,
            schema="DispatchRequest",
        )
    except A2AError as exc:
        raise HTTPException(502, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc


async def _client_a2a_handler(
    payload: dict[str, Any],
    schema: str,
    fwd: dict[str, str],
) -> tuple[dict[str, Any], str]:
    headers = _merge_auth_headers(fwd)
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        result = await _dispatch_to_orchestrator(client, payload, headers)
    return result, TaskState.SUBMITTED


mount_a2a(app, CLIENT_GATEWAY_CARD, _client_a2a_handler)


@app.get("/")
async def portal() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(404, "portal UI not found")
    return FileResponse(index)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "client-gateway", "portal": "/"}


@app.post("/v1/assistant/tasks", response_model=AssistantTaskResponse, status_code=202)
async def create_task(req: AssistantTaskRequest) -> AssistantTaskResponse:
    session_id = req.session_id or f"sess_{uuid.uuid4().hex[:12]}"
    headers = _merge_auth_headers(authorization=req.authorization)
    planned_playbook: str | None = req.playbook

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        body: dict[str, Any] = {
            "session_id": session_id,
            "message": req.message,
            "batch_id": req.batch_id,
            "factory_id": req.factory_id,
            "defect_type": req.defect_type,
            "skip_triage": req.skip_triage,
            "confirm_rca": req.confirm_rca,
            "hitl_approved": req.hitl_approved,
            "async_mode": True,
        }
        if should_invoke_planner(req):
            plan = await _plan_task(client, req, headers)
            planned_playbook = plan.get("playbook", req.playbook or "investigate")
            params = plan.get("params") or {}
            body["playbook"] = planned_playbook
            body["message"] = params.get("message") or req.message
            body["batch_id"] = params.get("batch_id") or req.batch_id
            body["factory_id"] = params.get("factory_id") or req.factory_id
            body["defect_type"] = params.get("defect_type") or req.defect_type
            body["confirm_rca"] = params.get("confirm_rca", req.confirm_rca)
            body["skip_triage"] = params.get("skip_triage", req.skip_triage)
            body["hitl_approved"] = params.get("hitl_approved", req.hitl_approved)
        else:
            body["playbook"] = req.playbook or "investigate"

        try:
            data = await _dispatch_to_orchestrator(client, body, headers)
        except httpx.RequestError as exc:
            raise HTTPException(502, f"orchestrator unreachable: {exc}") from exc

    return AssistantTaskResponse(
        task_id=session_id,
        session_id=session_id,
        trace_id=data.get("trace_id", ""),
        status=TaskState.SUBMITTED,
        sse_url=f"/v1/assistant/tasks/{session_id}/stream",
        poll_url=f"{settings.orchestrator_url}/a2a/v1/context/{session_id}",
        planned_playbook=planned_playbook,
    )


@app.post("/v1/assistant/tasks/{session_id}/resume", response_model=ResumeResponse)
async def resume_task(session_id: str, req: HitlResumeRequest) -> ResumeResponse:
    """HITL 签核：先调 RCA resolve（如有 thread_id），再 Orchestrator 续跑剧本。"""
    headers = _service_headers()

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        ctx_resp = await client.get(f"{settings.orchestrator_url}/a2a/v1/context/{session_id}")
        if ctx_resp.status_code == 404:
            raise HTTPException(404, "session not found")
        if ctx_resp.status_code >= 400:
            raise HTTPException(ctx_resp.status_code, ctx_resp.text)
        ctx = ctx_resp.json()

        thread_id = req.thread_id or (ctx.get("rca") or {}).get("thread_id")
        if thread_id and req.approved:
            feedback: dict[str, Any] = {
                "action": "approve",
                "comment": req.feedback,
            }
            if req.root_cause:
                feedback["selected_root_cause"] = req.root_cause
            delegator = A2AClient(client, headers=headers)
            try:
                await delegator.resume(
                    settings.rca_agent_url,
                    task_id=session_id,
                    thread_id=thread_id,
                    feedback=feedback,
                )
            except A2AError as exc:
                raise HTTPException(502, f"rca A2A resume failed: {exc}") from exc
            except Exception as exc:
                raise HTTPException(502, f"rca A2A resume failed: {exc}") from exc
            logger.info("hitl.rca_resumed", session_id=session_id, thread_id=thread_id)

        dispatch_body = {
            "session_id": session_id,
            "playbook": req.playbook,
            "message": "HITL 签核后续跑",
            "batch_id": ctx.get("batch_id"),
            "defect_type": ctx.get("defect_type"),
            "hitl_approved": req.approved,
            "confirm_rca": True,
            "async_mode": True,
        }
        if req.root_cause:
            dispatch_body["message"] = f"人工确认根因：{req.root_cause}"

        try:
            await _dispatch_to_orchestrator(client, dispatch_body, headers)
        except httpx.RequestError as exc:
            raise HTTPException(502, f"orchestrator unreachable: {exc}") from exc

    return ResumeResponse(
        session_id=session_id,
        status=TaskState.RUNNING,
        message="HITL 已提交，请继续监听 SSE",
        context=ctx,
    )


@app.get("/v1/assistant/tasks/{session_id}/stream")
async def stream_task(
    session_id: str,
    after_seq: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    if last_event_id and last_event_id.isdigit():
        after_seq = max(after_seq, int(last_event_id))

    async def proxy_stream():
        url = f"{settings.orchestrator_url}/a2a/v1/sessions/{session_id}/events"
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream("GET", url, params={"after_seq": after_seq}) as resp:
                    if resp.status_code >= 400:
                        yield f'event: error\ndata: {{"detail": "upstream {resp.status_code}"}}\n\n'
                        return
                    async for chunk in resp.aiter_text():
                        yield chunk
            except httpx.RequestError as exc:
                yield f'event: error\ndata: {{"detail": "orchestrator stream failed: {exc}"}}\n\n'

    return StreamingResponse(
        proxy_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
