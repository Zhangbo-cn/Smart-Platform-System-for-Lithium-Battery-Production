from __future__ import annotations

import asyncio
from typing import Any

from agent.graphs import build_quality_analysis_graph
from agent.state import EvidenceItem
from agent.tools.bootstrap import bootstrap_registry
from agent.tools.registry import ToolRegistry
from api.schemas import AnalysisRequest, AnalysisResponse
from api.tracing import build_langsmith_callbacks
from config import get_settings
from harness_core.audit.tracer import new_trace_id, set_trace_id
from harness_core.permission.checker import PermissionChecker
from harness.context.memory_harness import MemoryHarness
from api.task_store import InMemoryTaskStore
from harness.checkpoint import build_checkpointer
from harness.hitl import graph_config, interrupt_payload, is_interrupted, resume_command
from harness.hitl.broker import HITLBroker
from knowledge.fmea_registry import FMEARegistry


def to_response(trace_id: str, thread_id: str, final: dict[str, Any], **extra) -> AnalysisResponse:
    evidence_raw = final.get("evidence", []) or []
    evidence_out = [
        {
            "description": ev.get("description", ""),
            "source_tool": ev.get("source_tool", ""),
            "data_ref": ev.get("data_ref", ""),
            "confidence": ev.get("confidence", 0.0),
        }
        for ev in evidence_raw
    ]
    # 基础值从 final state 取，**extra 里的 key 会覆盖（如 HITL 场景覆写 status/requires_hitl）
    kwargs: dict[str, Any] = {
        "trace_id": trace_id,
        "thread_id": thread_id,
        "status": final.get("status", "done"),
        "root_cause": final.get("root_cause", ""),
        "recommendations": final.get("recommendations", []),
        "confidence": final.get("confidence", 0.0),
        "report_md": final.get("final_report", ""),
        "requires_hitl": final.get("requires_hitl", False),
        "evidence": evidence_out,
        "rca_artifacts": final.get("rca_artifacts"),
    }
    kwargs.update(extra)  # extra 覆盖默认值，避免 duplicate keyword
    return AnalysisResponse(**kwargs)


def prior_evidence_items(raw: list[dict[str, Any]]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for ev in raw:
        items.append(
            EvidenceItem(
                description=str(ev.get("description") or ev.get("summary") or ev.get("note", "")),
                source_tool=str(ev.get("source_tool") or ev.get("source", "prior")),
                data_ref=str(ev.get("data_ref") or ev.get("batch_id") or ""),
                confidence=float(ev.get("confidence", 0.85)),
            )
        )
    return items


def prior_tool_records(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """归一化为 ToolCallRecord 兼容 dict。"""
    out: list[dict[str, Any]] = []
    for i, call in enumerate(raw, start=1):
        out.append(
            {
                "step_id": call.get("step_id", -i),
                "tool": call.get("tool", ""),
                "args": call.get("args") or call.get("tool_args") or {},
                "result": call.get("result"),
                "duration_ms": call.get("duration_ms", 0),
                "error": call.get("error"),
                "_from_prior": True,
            }
        )
    return out


class AppServices:
    registry: ToolRegistry
    mcp_clients: list
    memory: MemoryHarness
    graph: Any
    hitl: HITLBroker
    mcp_connected: list[str]
    mcp_failed: list[str]
    checkpoint_backend: str
    a2a_tasks: InMemoryTaskStore


async def startup_services() -> AppServices:
    registry = ToolRegistry(permission_checker=PermissionChecker())
    mcp_clients, connected, failed = await bootstrap_registry(registry)
    memory = await MemoryHarness.create()
    await FMEARegistry.load()
    graph = build_quality_analysis_graph(registry, checkpointer=build_checkpointer())
    hitl = HITLBroker()
    svc = AppServices()
    svc.registry = registry
    svc.mcp_clients = mcp_clients
    svc.memory = memory
    svc.graph = graph
    svc.hitl = hitl
    svc.mcp_connected = connected
    svc.mcp_failed = failed
    svc.checkpoint_backend = get_settings().langgraph_checkpoint_backend
    svc.a2a_tasks = InMemoryTaskStore()
    return svc


async def shutdown_services(svc: AppServices) -> None:
    for client in svc.mcp_clients:
        await client.close()


async def run_quality_analysis(
    svc: AppServices,
    req: AnalysisRequest,
    *,
    user_id: str,
    user_role: str,
    trace_id: str | None = None,
) -> AnalysisResponse:
    tid = trace_id or new_trace_id()
    set_trace_id(tid)
    thread_id = req.session_id or tid

    memory_context = await svc.memory.build_planner_context(
        session_id=thread_id,
        user_id=user_id,
        query=req.user_query,
        defect_type=req.defect_type,
    )
    if req.batch_id:
        memory_context = f"【目标批次】{req.batch_id}\n\n{memory_context}"

    state: dict[str, Any] = {
        "trace_id": tid,
        "session_id": thread_id,
        "user_id": user_id,
        "user_role": user_role,
        "user_query": req.user_query,
        "batch_id": req.batch_id or "",
        "defect_type": req.defect_type or "",
        "memory_context": memory_context,
        "prior_tool_calls": prior_tool_records(req.prior_tool_calls),
        "evidence": prior_evidence_items(req.prior_evidence),
        "tool_calls": prior_tool_records(req.prior_tool_calls),
    }
    config = graph_config(thread_id)
    callbacks = build_langsmith_callbacks()
    invoke_config = {**config, "recursion_limit": 50}
    if callbacks:
        invoke_config["callbacks"] = callbacks

    try:
        final = await asyncio.wait_for(
            svc.graph.ainvoke(state, config=invoke_config),
            timeout=300.0,  # 5 分钟超时，防止 LLM 无限等待
        )
    except asyncio.TimeoutError:
        logger.error("rca.graph_timeout", thread_id=thread_id)
        return to_response(tid, thread_id, {
            "status": "failed",
            "root_cause": "",
            "confidence": 0.0,
            "final_report": "RCA analysis timed out after 5 minutes",
        })

    if is_interrupted(final):
        payload = interrupt_payload(final)
        request_id = svc.hitl.register_pending(thread_id, tid, payload)
        return to_response(
            tid,
            thread_id,
            final,
            status="hitl",
            requires_hitl=True,
            hitl_request_id=request_id,
            hitl_payload=payload,
        )

    await svc.memory.persist_analysis(thread_id, user_id, req.user_query, final)
    return to_response(tid, thread_id, final)


async def run_hitl_resolve(
    svc: AppServices,
    *,
    thread_id: str | None,
    request_id: str | None,
    user_id: str,
    feedback: dict[str, Any],
) -> AnalysisResponse:
    resolved_thread = thread_id
    if not resolved_thread and request_id:
        resolved_thread = svc.hitl.get_thread_id(request_id)
    if not resolved_thread:
        raise ValueError("thread_id or request_id required")

    config = graph_config(resolved_thread)
    callbacks = build_langsmith_callbacks()
    invoke_config = {**config, "recursion_limit": 50}
    if callbacks:
        invoke_config["callbacks"] = callbacks
    try:
        final = await asyncio.wait_for(
            svc.graph.ainvoke(resume_command(feedback), config=invoke_config),
            timeout=300.0,
        )
    except asyncio.TimeoutError:
        logger.error("rca.hitl_resolve_timeout", thread_id=resolved_thread)
        return to_response(resolved_thread, resolved_thread, {
            "status": "failed",
            "root_cause": "",
            "confidence": 0.0,
            "final_report": "HITL resume timed out",
        })

    trace_id = final.get("trace_id", resolved_thread)
    if is_interrupted(final):
        payload = interrupt_payload(final)
        hitl_id = svc.hitl.register_pending(resolved_thread, trace_id, payload)
        return to_response(
            trace_id,
            resolved_thread,
            final,
            status="hitl",
            requires_hitl=True,
            hitl_request_id=hitl_id,
            hitl_payload=payload,
        )

    svc.hitl.clear_pending(resolved_thread)
    query = final.get("user_query", "")
    if query:
        await svc.memory.persist_analysis(resolved_thread, user_id, query, final)

    return to_response(trace_id, resolved_thread, final)
