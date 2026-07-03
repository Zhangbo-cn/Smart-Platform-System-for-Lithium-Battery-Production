"""Planner 规则引擎（Tier 0 回退）；ReAct 见 react_agent.py。"""

from __future__ import annotations

import re

from platform_contracts.agent_network import AgentNetwork
from platform_contracts.agent_registry_seed import ALL_REGISTERED_AGENT_CARDS
from platform_contracts.plan_result import PlanParams, PlanResult, PlannerRequest

_BATCH_RE = re.compile(r"[Bb](?:atch)?[-_]?\d{4,}", re.I)
_IMPLEMENTED = frozenset({"investigate", "trace_only", "rca", "close_loop"})


def list_playbooks() -> list[dict[str, str]]:
    return [
        {"id": "trace_only", "description": "批次查询"},
        {"id": "investigate", "description": "深度分析：分诊→追溯→RCA"},
        {"id": "rca", "description": "单点根因分析"},
        {"id": "close_loop", "description": "质量闭环：RCA→8D→QMS"},
        {"id": "shift_patrol", "description": "开班巡检（未实现）"},
        {"id": "coating_incident", "description": "涂布异常（未实现）"},
        {"id": "pm_alert", "description": "设备预警（未实现）"},
    ]


def get_service_card(name: str) -> dict:
    net = AgentNetwork.from_cards(ALL_REGISTERED_AGENT_CARDS)
    card = net.get_card(name)
    return card.model_dump(mode="json")


def _extract_batch_id(message: str, given: str | None) -> str | None:
    if given:
        return given
    m = _BATCH_RE.search(message)
    return m.group(0).upper().replace("BATCH", "B").replace("_", "").replace("-", "") if m else None


def plan(req: PlannerRequest) -> PlanResult:
    """Tier 0/1 规则；不 delegate 业务服务。"""
    msg = req.message.strip()
    batch_id = _extract_batch_id(msg, req.batch_id)

    if req.playbook and req.playbook in _IMPLEMENTED:
        if batch_id or req.playbook in ("rca",):
            return PlanResult(
                playbook=req.playbook,  # type: ignore[arg-type]
                params=PlanParams(
                    message=msg or req.playbook,
                    batch_id=batch_id,
                    factory_id=req.factory_id,
                    defect_type=req.defect_type,
                    confirm_rca=req.playbook == "investigate",
                ),
                confidence=0.95,
                reasoning="调用方已指定 playbook，仅补全参数",
            )

    lower = msg.lower()
    if any(k in lower for k in ("查批次", "追溯", "流转", "trace")):
        pb = "trace_only"
    elif any(k in lower for k in ("8d", "闭环", "capa", "定稿")):
        pb = "close_loop"
    elif any(k in lower for k in ("根因", "rca", "为什么", "原因")):
        pb = "investigate" if batch_id else "rca"
    else:
        pb = "investigate"

    if pb not in _IMPLEMENTED:
        pb = "investigate"

    return PlanResult(
        playbook=pb,  # type: ignore[arg-type]
        params=PlanParams(
            message=msg,
            batch_id=batch_id,
            factory_id=req.factory_id,
            defect_type=req.defect_type,
            confirm_rca=pb == "investigate",
        ),
        confidence=0.75 if batch_id else 0.6,
        reasoning=f"规则匹配 playbook={pb}",
    )
