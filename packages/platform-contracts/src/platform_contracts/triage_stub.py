"""分诊预留接口（无独立 triage-agent 进程时使用）。

协作约定（当前竖切）：
  Client / 工程师 ── defect_type? ──► Router PlatformContext
  Router ── resolve_triage() ──► 写 defect_type / severity / triage_result
  Router ── RcaInvokeRequest ──► quality-rca-agent（FMEA 预填 defect_type）

未来若恢复独立 triage-agent：
  Router 改 A2A 委派 TriageRequest → triage-agent → TriageResponse，本 stub 可删除。
"""

from __future__ import annotations

import re
from typing import Literal

from platform_contracts.agent_handoffs import TriageRequest, TriageResponse

_KEYWORD_DEFECTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"涂布|面密度|coating", re.I), "coating_density_low"),
    (re.compile(r"容量|capacity", re.I), "capacity_low"),
    (re.compile(r"内阻|阻抗", re.I), "ir_high"),
    (re.compile(r"短路|short", re.I), "short_circuit"),
]


def _defect_from_query(query: str) -> str:
    for pattern, defect in _KEYWORD_DEFECTS:
        if pattern.search(query):
            return defect
    return "unknown_defect"


def resolve_triage(
    *,
    query: str,
    batch_id: str | None = None,
    defect_type: str | None = None,
    session_id: str = "",
) -> TriageResponse:
    """Router 内联分诊：优先用调用方已给的 defect_type，否则从 query 关键词推断。"""
    dt = (defect_type or "").strip() or _defect_from_query(query)
    suggest: Literal["trace", "rca", "none"] = "trace" if batch_id else "rca"
    return TriageResponse(
        defect_type=dt,
        severity="medium",
        suggest_next=suggest,
        stub=True,
    )


def triage_request_from_dispatch(
    *,
    session_id: str,
    message: str,
    batch_id: str | None,
    defect_type: str | None = None,
) -> TriageRequest:
    """与将来 triage-agent A2A 入参对齐。"""
    return TriageRequest(
        session_id=session_id,
        query=message,
        batch_id=batch_id,
    )
