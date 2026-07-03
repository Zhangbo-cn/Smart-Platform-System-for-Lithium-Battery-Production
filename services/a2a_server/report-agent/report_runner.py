"""8D 报告生成 + QMS MCP 写回。"""

from __future__ import annotations

import json
import time
from typing import Any

from harness_core.registry import ToolRegistry
from platform_contracts.agent_handoffs import Report8dRequest

_AGENT_USER = "report-reporter-agent"
_AGENT_ROLE = "quality_manager"


def _unwrap_mcp_result(raw: Any) -> Any:
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict) and "text" in first:
            try:
                return json.loads(first["text"])
            except (json.JSONDecodeError, TypeError):
                return first.get("text", raw)
    return raw


def _build_report_md(req: Report8dRequest) -> str:
    evidence_lines = []
    for i, ev in enumerate(req.evidence[:8], start=1):
        desc = ev.get("description") or ev.get("note") or ev.get("source_tool") or str(ev)
        evidence_lines.append(f"{i}. {desc}")

    recommendations = [
        "复核根因相关工序参数并留存记录",
        "对同批次在制品加强抽检",
        "更新 FMEA 命中项并安排复盘",
    ]
    if req.confidence is not None and req.confidence < 0.75:
        recommendations.insert(0, "低置信度：建议质量经理二次签核后再关闭 CAPA")

    md = (
        f"# 8D 质量报告\n\n"
        f"**Session**: `{req.session_id}`  \n"
        f"**置信度**: {req.confidence if req.confidence is not None else 'N/A'}\n\n"
        f"## D2 问题描述\n"
        f"已确认质量异常，进入 CAPA 定稿流程。\n\n"
        f"## D3 临时措施\n"
        f"- 隔离可疑在制品\n"
        f"- 通知产线班组长\n\n"
        f"## D4 根因\n"
        f"{req.root_cause}\n\n"
        f"## D5 纠正措施\n"
    )
    for rec in recommendations:
        md += f"- {rec}\n"
    md += "\n## D6 证据摘要\n"
    md += "\n".join(evidence_lines) if evidence_lines else "- （无结构化证据，见 RCA 会话）\n"
    return md, recommendations


async def run_report_8d(
    registry: ToolRegistry,
    req: Report8dRequest,
) -> tuple[str, list[str], str | None, str | None]:
    report_md, recommendations = _build_report_md(req)

    title = f"8D-{req.session_id[-8:]}"
    started = time.perf_counter()
    draft_raw = await registry.invoke(
        "qms.create_8d_draft",
        {
            "session_id": req.session_id,
            "title": title,
            "report_md": report_md,
            "root_cause": req.root_cause,
        },
        user_id=_AGENT_USER,
        user_role=_AGENT_ROLE,
    )
    draft = _unwrap_mcp_result(draft_raw)
    capa_id = draft.get("capa_id") if isinstance(draft, dict) else None

    qms_status = "draft"
    if capa_id:
        status_raw = await registry.invoke(
            "qms.update_capa_status",
            {"capa_id": capa_id, "status": "pending_approval", "comment": "8D draft submitted"},
            user_id=_AGENT_USER,
            user_role=_AGENT_ROLE,
        )
        status = _unwrap_mcp_result(status_raw)
        if isinstance(status, dict):
            qms_status = str(status.get("status", qms_status))

    _ = int((time.perf_counter() - started) * 1000)
    return report_md, recommendations, capa_id, qms_status
