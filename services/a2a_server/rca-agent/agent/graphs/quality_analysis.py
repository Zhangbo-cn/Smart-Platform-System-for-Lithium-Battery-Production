from __future__ import annotations

import re
from typing import Any

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent.agents import ExecutorAgent, PlannerAgent, ReflectorAgent, ReporterAgent
from agent.state import QualityAnalysisState
from agent.tools.registry import ToolRegistry
from harness_core.context.compressor import ContextCompressor

logger = structlog.get_logger(__name__)

# Triage 规则（内联，避免依赖 triage-agent 外部服务）
_KEYWORD_DEFECTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"涂布|面密度|coating", re.I), "coating_density_low"),
    (re.compile(r"容量|capacity|衰减|循环", re.I), "capacity_low"),
    (re.compile(r"内阻|阻抗|ir|电阻", re.I), "ir_high"),
    (re.compile(r"短路|short|微短|刺穿", re.I), "short_circuit"),
    (re.compile(r"析锂|lithium.*plat|锂枝晶", re.I), "lithium_plating"),
    (re.compile(r"注液|电解液|浸润|leak|漏液", re.I), "electrolyte_leakage"),
    (re.compile(r"鼓胀|swelling|变形|凸起", re.I), "swelling"),
    (re.compile(r"虚焊|焊接|焊点|极耳|weld", re.I), "weld_defect"),
    (re.compile(r"毛刺|burr|分切|cutting", re.I), "burr_excessive"),
    (re.compile(r"水分|湿度|moisture|ppm.*水", re.I), "moisture_exceed"),
    (re.compile(r"隔膜|separator|闭孔|收缩", re.I), "separator_defect"),
    (re.compile(r"辊压|压实|孔隙率|density|过压", re.I), "rolling_overpressure"),
    (re.compile(r"搅拌|分散|团聚|disperse|浆料|slurry", re.I), "mixing_uneven"),
    (re.compile(r"烘箱|oven|温度.*高|干燥|curing", re.I), "drying_abnormal"),
    (re.compile(r"来料|ncm|正极|负极|anode|cathode|材料", re.I), "raw_material_issue"),
    (re.compile(r"化成|sei|formation|老化|aging", re.I), "formation_abnormal"),
]

_SEVERITY_KEYWORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"起火|爆炸|安全|smoke|fire|safety", re.I), "critical"),
    (re.compile(r"批量|整批|全部|大批|all.*batch|全线", re.I), "high"),
    (re.compile(r"历史复现|再发|repeat|again|recur|又出现", re.I), "high"),
    (re.compile(r"个别|偶发|偶尔|sporadic|once", re.I), "low"),
]


def _triage_defect(query: str) -> str:
    for pattern, defect in _KEYWORD_DEFECTS:
        if pattern.search(query):
            return defect
    return "unknown_defect"


def _triage_severity(query: str, defect: str) -> str:
    for pattern, sev in _SEVERITY_KEYWORDS:
        if pattern.search(query):
            return sev
    return "medium" if defect != "unknown_defect" else "low"


# ── 新增节点 ──────────────────────────────────────────────


async def _triage_node(state: QualityAnalysisState) -> dict:
    """意图识别 + 缺陷类型判断（替代外部 triage-agent 调用）。

    如果请求已提供 defect_type，直接使用；否则规则匹配。
    """
    query = state.get("user_query", "")
    existing = state.get("defect_type") or ""

    if existing and existing != "unknown_defect":
        defect = existing
    else:
        defect = _triage_defect(query)

    severity = _triage_severity(query, defect)

    logger.info("triage_node.result", defect=defect, severity=severity, query=query[:50])

    return {
        "defect_type": defect,
        "severity": severity,
        "status": "triaged",
    }


async def _gather_node(state: QualityAnalysisState) -> dict:
    """初始数据采集：批次追溯 + 工艺参数（替代外部 trace-worker 调用）。

    使用已有 ToolRegistry 直接调 MCP 工具，不走 A2A。
    适合纯数据查询场景。
    """
    batch_id = state.get("batch_id", "")
    tool_calls = list(state.get("tool_calls") or [])
    evidence = list(state.get("evidence") or [])

    if not batch_id:
        return {"status": "no_batch", "tool_calls": tool_calls, "evidence": evidence}

    registry = state.get("_registry")
    user_id = state.get("user_id", "rca-agent")
    user_role = state.get("user_role", "quality_manager")

    # 批次追溯
    try:
        result = await registry.invoke(
            "mes.query_batch_trace", {"batch_id": batch_id},
            user_id=user_id, user_role=user_role,
        )
        tool_calls.append({
            "step_id": -2, "tool": "mes.query_batch_trace",
            "args": {"batch_id": batch_id}, "result": result,
            "duration_ms": 0, "error": None,
        })
        if isinstance(result, list):
            stations = [s.get("step", "") for s in result if isinstance(s, dict)]
            evidence.append({
                "description": f"批次追溯: {', '.join(stations)}",
                "source_tool": "mes.query_batch_trace", "confidence": 0.9,
            })
    except Exception as exc:
        logger.warning("gather.batch_trace_failed", error=str(exc))

    # 工艺参数
    try:
        result = await registry.invoke(
            "mes.get_process_params", {"batch_id": batch_id},
            user_id=user_id, user_role=user_role,
        )
        tool_calls.append({
            "step_id": -1, "tool": "mes.get_process_params",
            "args": {"batch_id": batch_id}, "result": result,
            "duration_ms": 0, "error": None,
        })
    except Exception as exc:
        logger.warning("gather.process_params_failed", error=str(exc))

    return {
        "status": "gathered",
        "tool_calls": tool_calls,
        "evidence": evidence,
        "prior_tool_calls": tool_calls,
        "prior_evidence": evidence,
    }


# ── 原节点 ────────────────────────────────────────────────


def _route_after_reflector(state: QualityAnalysisState) -> str:
    if state.get("need_more_data"):
        return "executor"
    if state.get("requires_hitl"):
        return "hitl"
    return "reporter"


async def _hitl_node(state: QualityAnalysisState) -> dict:
    payload = {
        "trace_id": state.get("trace_id"),
        "confidence": state.get("confidence", 0.0),
        "partial_result": state.get("partial_result"),
        "evidence": state.get("evidence", []),
        "root_cause": state.get("root_cause", ""),
        "defect_type": state.get("defect_type"),
    }
    feedback = interrupt(payload)
    if not isinstance(feedback, dict):
        feedback = {"approved": bool(feedback), "feedback": str(feedback)}

    updates: dict = {
        "hitl_response": feedback,
        "requires_hitl": False,
        "status": "reporting",
    }
    if feedback.get("root_cause"):
        updates["root_cause"] = feedback["root_cause"]
    elif feedback.get("approved") and state.get("partial_result", {}).get("suspected"):
        suspected = state["partial_result"]["suspected"]
        updates["root_cause"] = f"人工确认疑似根因：{suspected[0]}"
    if feedback.get("recommendations"):
        updates["recommendations"] = feedback["recommendations"]
    return updates


def build_quality_analysis_graph(
    registry: ToolRegistry,
    compressor: ContextCompressor | None = None,
    checkpointer=None,
):
    planner = PlannerAgent(registry=registry)
    executor = ExecutorAgent(registry=registry, compressor=compressor or ContextCompressor())
    reflector = ReflectorAgent()
    reporter = ReporterAgent()

    graph = StateGraph(QualityAnalysisState)
    graph.add_node("triage", _triage_node)
    graph.add_node("gather", _gather_node)
    graph.add_node("planner", planner.run)
    graph.add_node("executor", executor.run)
    graph.add_node("reflector", reflector.run)
    graph.add_node("hitl", _hitl_node)
    graph.add_node("reporter", reporter.run)

    # 新流程: START → triage → gather → planner → executor → ...
    graph.add_edge(START, "triage")
    graph.add_edge("triage", "gather")
    graph.add_edge("gather", "planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "reflector")
    graph.add_conditional_edges(
        "reflector",
        _route_after_reflector,
        {"executor": "executor", "hitl": "hitl", "reporter": "reporter"},
    )
    graph.add_edge("hitl", "reporter")
    graph.add_edge("reporter", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())
