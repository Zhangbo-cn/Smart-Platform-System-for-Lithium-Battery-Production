from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from python_a2a import Task

from api.a2a_hitl import a2a_feedback_to_hitl, analysis_status_to_task_state
from api.auth import TokenPayload, decode_token
from api.handlers import run_hitl_resolve, run_quality_analysis, shutdown_services, startup_services
from api.schemas import AnalysisRequest, AnalysisResponse, HITLResolveRequest
from harness_core.a2a import mount_a2a, result_to_task
from platform_contracts.agent_card import RCA_AGENT_CARD
from platform_contracts.task_state import TaskState

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.services = await startup_services()
    logger.info(
        "api.startup_complete",
        mcp_connected=app.state.services.mcp_connected,
        mcp_failed=app.state.services.mcp_failed,
    )
    try:
        yield
    finally:
        await shutdown_services(app.state.services)


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


async def _a2a_quality_handler(
    payload: dict[str, Any],
    schema: str | None,
    fwd: dict[str, str],
) -> tuple[dict[str, Any], str] | dict[str, Any]:
    principal = _principal_from_headers(fwd)
    trace = fwd.get("x-trace-id") or fwd.get("X-Trace-Id")
    req = AnalysisRequest(**payload)
    svc = app.state.services
    try:
        resp = await run_quality_analysis(
            svc, req, user_id=principal.sub, user_role=principal.role, trace_id=trace,
        )
    except Exception as exc:
        logger.exception("rca.analysis_failed", session_id=req.session_id, error=str(exc))
        return {"error": str(exc), "root_cause": "", "confidence": 0.0}, TaskState.FAILED

    task_id = req.session_id or resp.thread_id
    if task_id:
        state = analysis_status_to_task_state(resp.status)
        task = Task(id=task_id, session_id=task_id)
        result_to_task(task, resp.model_dump(mode="json"), state=state)
        svc.a2a_tasks.save(task_id=task_id, task_dict=task.to_dict(), analysis=resp, status=state)
    result = resp.model_dump(mode="json")
    if resp.requires_hitl:
        return result, TaskState.INPUT_REQUIRED
    return result, TaskState.COMPLETED


async def _a2a_resume_handler(
    task_id: str,
    thread_id: str,
    feedback: dict[str, Any],
    fwd: dict[str, str],
) -> tuple[dict[str, Any], str] | dict[str, Any]:
    principal = _principal_from_headers(fwd)
    svc = app.state.services
    stored = svc.a2a_tasks.get(task_id)
    if stored is None:
        raise HTTPException(404, f"task not found: {task_id}")
    if stored.status != TaskState.INPUT_REQUIRED:
        raise HTTPException(409, f"task {task_id} is not INPUT_REQUIRED")

    resp = await run_hitl_resolve(
        svc,
        thread_id=thread_id,
        request_id=stored.analysis.hitl_request_id,
        user_id=principal.sub,
        feedback=a2a_feedback_to_hitl(feedback),
    )
    state = analysis_status_to_task_state(resp.status)
    task = Task(id=task_id, session_id=task_id)
    result_to_task(task, resp.model_dump(mode="json"), state=state)
    svc.a2a_tasks.save(
        task_id=task_id,
        task_dict=task.to_dict(),
        analysis=resp,
        status=state,
    )
    result = resp.model_dump(mode="json")
    if resp.requires_hitl:
        return result, TaskState.INPUT_REQUIRED
    return result, TaskState.COMPLETED


async def _a2a_task_lookup(task_id: str, _fwd: dict[str, str]) -> dict[str, Any] | None:
    stored = app.state.services.a2a_tasks.get(task_id)
    if stored is None:
        return None
    return stored.task_dict


mount_a2a(
    app,
    RCA_AGENT_CARD,
    _a2a_quality_handler,
    resume_handler=_a2a_resume_handler,
    task_lookup=_a2a_task_lookup,
)


def auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> TokenPayload:
    try:
        return decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc


@app.get("/health")
async def health():
    svc = app.state.services
    status = "ok" if not svc.mcp_failed else "degraded"
    return {
        "status": status,
        "agent": "quality-rca-agent",
        "mcp_connected": svc.mcp_connected,
        "mcp_failed": svc.mcp_failed,
        "checkpoint_backend": svc.checkpoint_backend,
    }


@app.post("/v1/analysis/quality", response_model=AnalysisResponse)
async def quality_analysis(
    req: AnalysisRequest,
    principal: TokenPayload = Depends(auth),
    x_trace_id: str | None = Header(default=None, alias="X-Trace-Id"),
) -> AnalysisResponse:
    return await run_quality_analysis(
        app.state.services,
        req,
        user_id=principal.sub,
        user_role=principal.role,
        trace_id=x_trace_id,
    )


@app.post("/v1/hitl/resolve", response_model=AnalysisResponse)
async def hitl_resolve(req: HITLResolveRequest, principal: TokenPayload = Depends(auth)):
    feedback: dict[str, Any] = {
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

    try:
        return await run_hitl_resolve(
            app.state.services,
            thread_id=req.thread_id,
            request_id=req.request_id,
            user_id=principal.sub,
            feedback=feedback,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


if __name__ == "__main__":
    import asyncio

    import uvicorn
    from config import get_settings

    port = get_settings().api_port
    config = uvicorn.Config(app, host="0.0.0.0", port=port)
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
