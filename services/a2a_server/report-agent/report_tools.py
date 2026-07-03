"""Reporter 进程内工具（非 MCP）：锁定根因、知识检索 stub。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

_DATA_DIR = Path(__file__).parent / "data"
_LOCKED: dict[str, Any] = {}


def bind_report_context(req_dict: dict[str, Any]) -> None:
    """每次 A2A 请求前绑定只读上下文。"""
    _LOCKED.clear()
    _LOCKED.update(req_dict)


@tool
def get_locked_root_cause() -> str:
    """返回 RCA 已锁定根因文本，禁止改写。"""
    return str(_LOCKED.get("root_cause") or "")


@tool
def get_rca_artifacts() -> str:
    """返回 RCA Reporter 节点产出的结构化 artifact JSON。"""
    artifacts = _LOCKED.get("rca_artifacts") or {}
    return json.dumps(artifacts, ensure_ascii=False, default=str)


@tool
def search_sop(defect_type: str = "", keyword: str = "") -> str:
    """检索 SOP/作业指导书片段（进程内 stub）。"""
    sop_path = _DATA_DIR / "sop_snippets.json"
    if not sop_path.exists():
        return json.dumps({"hits": [], "note": "sop stub empty"})
    data = json.loads(sop_path.read_text(encoding="utf-8"))
    hits = []
    for item in data.get("items", []):
        if defect_type and defect_type not in item.get("defect_types", []):
            continue
        if keyword and keyword not in item.get("title", "") + item.get("body", ""):
            continue
        hits.append(item)
    return json.dumps({"hits": hits[:3]}, ensure_ascii=False)


@tool
def search_golden_case(defect_type: str = "", query: str = "") -> str:
    """检索历史 Golden Case 参考（进程内 stub）。"""
    golden_path = _DATA_DIR / "golden_8d_refs.json"
    if not golden_path.exists():
        return json.dumps({"hits": [], "note": "golden stub empty"})
    data = json.loads(golden_path.read_text(encoding="utf-8"))
    hits = []
    for item in data.get("cases", []):
        if defect_type and defect_type != item.get("defect_type"):
            continue
        if query and query.lower() not in item.get("root_cause", "").lower():
            continue
        hits.append(item)
    return json.dumps({"hits": hits[:3]}, ensure_ascii=False)


INTERNAL_REPORT_TOOLS = [
    get_locked_root_cause,
    get_rca_artifacts,
    search_sop,
    search_golden_case,
]
