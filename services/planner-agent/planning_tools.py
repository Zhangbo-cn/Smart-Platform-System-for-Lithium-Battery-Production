"""Planner 规划工具（进程内，非 MCP）。

支持动态刷新 playbook 列表：启动时从 Capability Registry 拉取已注册的 Agent，
根据其 capabilities 动态生成可选的 playbook 列表。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from plan_engine import get_service_card, list_playbooks

logger = structlog.get_logger(__name__)

# 基础 playbook（始终可用）
_BASE_PLAYBOOKS = ["investigate", "trace_only", "rca", "close_loop"]
# 运行时可以动态扩展
_IMPLEMENTED: set[str] = set(_BASE_PLAYBOOKS)


async def refresh_playbooks_from_registry(registry_url: str) -> None:
    """从 Capability Registry 拉取 Agent 能力，动态扩展可用 playbook。

    每个已注册 Agent 的 capabilities 可能对应新的 playbook 入口。
    例如注册了 patrol-agent → 新增 patrol playbook。
    """
    global _IMPLEMENTED
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{registry_url.rstrip('/')}/registry/agents")
            if resp.status_code != 200:
                return
            data = resp.json()
            # 从每个 Agent 的 capabilities 推断可支持的 playbook
            agents = data if isinstance(data, list) else data.get("agents", data)
            for agent in agents:
                if isinstance(agent, dict):
                    enabled = agent.get("enabled", True)
                    for cap in agent.get("capabilities", []):
                        if isinstance(cap, str) and cap not in _IMPLEMENTED:
                            _IMPLEMENTED.add(cap)
    except Exception as exc:
        logger.warning("planner.refresh_failed", registry=registry_url, error=str(exc))


def build_tool_schemas() -> list[dict[str, Any]]:
    """构建 Tool Schema，playbook enum 动态从 _IMPLEMENTED 生成。"""
    playbook_enum = sorted(_IMPLEMENTED)
    return [
        {
            "type": "function",
            "function": {
                "name": "list_playbooks",
                "description": "列出平台 Playbook 及说明；优先选择 implemented=Y 的剧本。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_capability_card",
                "description": "读取某 A2A 能力服务的 AgentCard（capabilities、enabled），用于判断剧本是否可执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "服务名，如 trace-worker、quality-rca-agent、report-8d-worker",
                        }
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_plan",
                "description": f"提交最终规划结果。必须从已实现剧本中选择：{', '.join(playbook_enum)}。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "playbook": {
                            "type": "string",
                            "enum": playbook_enum,
                        },
                        "message": {"type": "string"},
                        "batch_id": {"type": "string"},
                        "factory_id": {"type": "string"},
                        "defect_type": {"type": "string"},
                        "confirm_rca": {"type": "boolean"},
                        "skip_triage": {"type": "boolean"},
                        "hitl_approved": {"type": "boolean"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["playbook", "message", "confidence", "reasoning"],
                },
            },
        },
    ]


# 向后兼容：保留 TOOL_SCHEMAS 名称，但改为函数调用
TOOL_SCHEMAS: list[dict[str, Any]] = build_tool_schemas()


def execute_planning_tool(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """执行规划工具。submit_plan 返回 (json, plan_dict)；其它返回 (json, None)。"""
    if name == "list_playbooks":
        rows = []
        for row in list_playbooks():
            implemented = "Y" if row["id"] in _IMPLEMENTED else "N"
            rows.append({**row, "implemented": implemented})
        return json.dumps({"playbooks": rows}, ensure_ascii=False), None

    if name == "get_capability_card":
        service_name = str(arguments.get("name", "")).strip()
        if not service_name:
            return json.dumps({"error": "name required"}, ensure_ascii=False), None
        try:
            card = get_service_card(service_name)
        except KeyError:
            return json.dumps({"error": f"unknown service: {service_name}"}, ensure_ascii=False), None
        return json.dumps(card, ensure_ascii=False), None

    if name == "submit_plan":
        playbook = str(arguments.get("playbook", "")).strip()
        if playbook not in _IMPLEMENTED:
            return json.dumps({"error": f"playbook not implemented: {playbook}"}, ensure_ascii=False), None
        plan = {
            "playbook": playbook,
            "params": {
                "message": arguments.get("message", ""),
                "batch_id": arguments.get("batch_id"),
                "factory_id": arguments.get("factory_id"),
                "defect_type": arguments.get("defect_type"),
                "confirm_rca": bool(arguments.get("confirm_rca", playbook == "investigate")),
                "skip_triage": bool(arguments.get("skip_triage", False)),
                "hitl_approved": bool(arguments.get("hitl_approved", False)),
            },
            "confidence": float(arguments.get("confidence", 0.8)),
            "reasoning": str(arguments.get("reasoning", "")),
        }
        return json.dumps({"status": "accepted", "playbook": playbook}, ensure_ascii=False), plan

    return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False), None
