"""RCA Agent 数据处理辅助函数 — 被 RcaAgentServer.build_input / extract_output 引用。"""

from __future__ import annotations

from typing import Any

from agent.state import EvidenceItem


def prior_evidence_items(raw: list[dict[str, Any]]) -> list[EvidenceItem]:
    """归一化 prior_evidence 为 EvidenceItem 列表。"""
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
