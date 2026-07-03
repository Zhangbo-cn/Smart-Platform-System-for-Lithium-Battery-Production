from typing import Any, Literal

from pydantic import BaseModel, Field


class RcaContext(BaseModel):
    thread_id: str | None = None
    status: Literal["pending", "hitl", "done"] | None = None
    root_cause: str | None = None
    confidence: float | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    rca_artifacts: dict[str, Any] | None = None


class Report8dContext(BaseModel):
    report_md: str | None = None
    capa_id: str | None = None
    qms_status: str | None = None
    generation_mode: Literal["deep_agent", "template"] | None = None


class PlatformContext(BaseModel):
    """对齐 docs/AGENT_CATALOG.md §3；跨 Agent Session 黑板。

    核心字段（稳定）：session_id, trace_id, batch_id, task_status, current_step。
    域专用字段（版本化）：rca, report_8d — 存量依赖，保持兼容。
    扩展字段：artifacts — 新 Agent 产出走 blob，不需改 Schema。
    """

    session_id: str
    trace_id: str | None = None
    tenant_id: str | None = None
    factory_id: str | None = None
    batch_id: str | None = None
    line_id: str | None = None
    defect_type: str | None = None
    severity: str | None = None
    triage_result: dict[str, Any] | None = None
    prior_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    prior_evidence: list[dict[str, Any]] = Field(default_factory=list)
    rca: RcaContext = Field(default_factory=RcaContext)
    report_8d: Report8dContext = Field(default_factory=Report8dContext)
    # 通用扩展槽：key = agent name (如 "equipment-health-agent")，value = 该 Agent 的产出 blob
    artifacts: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展 Agent 产出（key=agent_name, value=产出 dict）。新增 Agent 不需改 Schema。",
    )
    task_status: str | None = None
    current_step: str | None = None
    fmea_version: str | None = None
