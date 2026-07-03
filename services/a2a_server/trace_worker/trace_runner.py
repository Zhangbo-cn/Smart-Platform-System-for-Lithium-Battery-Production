"""批次追溯：MCP 取证，输出 prior_evidence。"""

from __future__ import annotations

import json
from typing import Any

from harness_core.tool_registry import ToolRegistry
from platform_contracts.agent_handoffs import TraceRequest, TraceResponse

_SERVICE = "trace-worker"
_USER = "trace-worker"
_ROLE = "quality_engineer"


def _unwrap(raw: Any) -> Any:
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict) and "text" in first:
            try:
                return json.loads(first["text"])
            except (json.JSONDecodeError, TypeError):
                return first.get("text", raw)
    return raw


async def run_trace(registry: ToolRegistry, req: TraceRequest) -> TraceResponse:
    evidence: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []

    trace_raw = await registry.invoke(
        "mes.query_batch_trace",
        {"batch_id": req.batch_id},
        user_id=_USER,
        user_role=_ROLE,
    )
    trace_data = _unwrap(trace_raw)
    tool_calls.append({"tool": "mes.query_batch_trace", "args": {"batch_id": req.batch_id}})
    evidence.append(
        {
            "source_tool": "mes.query_batch_trace",
            "batch_id": req.batch_id,
            "description": f"批次追溯 {len(trace_data.get('stations', []))} 个工站",
            "data": trace_data,
        }
    )

    params_raw = await registry.invoke(
        "mes.get_process_params",
        {"batch_id": req.batch_id, "process_step": "coating"},
        user_id=_USER,
        user_role=_ROLE,
    )
    params_data = _unwrap(params_raw)
    tool_calls.append(
        {"tool": "mes.get_process_params", "args": {"batch_id": req.batch_id, "process_step": "coating"}}
    )
    evidence.append(
        {
            "source_tool": "mes.get_process_params",
            "batch_id": req.batch_id,
            "description": "涂布工序参数",
            "data": params_data,
        }
    )

    summary = req.query or f"批次 {req.batch_id} 追溯完成，证据 {len(evidence)} 条"
    return TraceResponse(
        tool_calls=tool_calls,
        evidence=evidence,
        summary=summary,
        stub=False,
    )
