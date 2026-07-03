from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages


class PlanStep(TypedDict):
    step_id: int
    action: str
    tool: str | None
    tool_args: dict[str, Any]
    rationale: str


class ToolCallRecord(TypedDict):
    step_id: int
    tool: str
    args: dict[str, Any]
    result: Any
    duration_ms: int
    error: str | None


class EvidenceItem(TypedDict):
    description: str
    source_tool: str
    data_ref: str
    confidence: float


class QualityAnalysisState(TypedDict, total=False):
    trace_id: str
    session_id: str
    user_id: str
    user_role: str
    user_query: str
    defect_type: str  # 缺陷类型，决定用哪棵 FMEA 因果树
    batch_id: str  # 目标批次（Router / trace-agent 传入）
    memory_context: str  # Harness 组装的短/中/长期记忆上下文
    prior_tool_calls: list[ToolCallRecord]  # trace-agent 前置取证
    started_at: datetime

    messages: Annotated[list, add_messages]

    analysis_plan: list[PlanStep]  # 步骤计划
    current_step: int  # 当前步骤编号

    tool_calls: list[ToolCallRecord]  # executor调用MCP之后的结果
    evidence: list[EvidenceItem]

    reflection_loops: int  # 当前反思轮数
    max_reflection_loops: int  # 动态补查预算（由 FMEA 验证器按命中链路算出）
    need_more_data: bool  # 是否需要更多数据(reflection反思判断)
    additional_queries: list[PlanStep]  # 需要直接查询的步骤
    refine_mode: str  # 本轮补查策略：DEEPEN / CORRELATE / REPLAN / CONFIRM / DEGRADE

    confidence: float
    requires_hitl: bool
    hitl_response: dict[str, Any] | None
    partial_result: dict[str, Any] | None  # 优雅降级时的"已排除/疑似"清单

    root_cause: str
    recommendations: list[str]
    final_report: str
    rca_artifacts: dict[str, Any]

    status: Literal["planning", "executing", "reflecting", "hitl", "reporting", "done", "failed"]
    error: str | None
