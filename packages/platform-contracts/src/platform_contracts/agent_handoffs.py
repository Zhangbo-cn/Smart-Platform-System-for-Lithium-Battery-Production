"""跨 Agent 交接 DTO：仅定义 Router 写入 Context、RCA/8D 消费的字段。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


# ----- trace-agent → RCA -----


class TraceRequest(BaseModel):
    session_id: str
    batch_id: str
    query: str | None = None
    scopes: list[str] = Field(default_factory=list)


class TraceResponse(BaseModel):
    """Router 合并进 PlatformContext.prior_evidence / prior_tool_calls"""

    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    stub: bool = True


# ----- triage（预留；独立 triage-agent 未部署时 Router 用 triage_stub.resolve_triage） -----


class TriageRequest(BaseModel):
    session_id: str
    query: str
    batch_id: str | None = None


class NextAgent(BaseModel):
    """Agent 建议的下游步骤。支持直接 A2A 调用或 Orchestrator 中转。"""
    agent_name: str
    agent_url: str | None = None
    step_name: str = ""
    confidence: float = 1.0
    payload_hint: dict[str, Any] = Field(default_factory=dict)


class TriageResponse(BaseModel):
    """Router 合并进 PlatformContext.defect_type / severity / triage_result"""

    defect_type: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    suggest_next: Literal["trace", "rca", "none"] = "trace"
    stub: bool = True
    next_agents: list[NextAgent] = Field(
        default_factory=list,
        description="Triage 建议的下游 Agent 列表。"
        "Orchestrator 可据此直接路由，无需硬编码 playbook。"
    )


# ----- RCA → report-8d-agent -----


class RcaArtifactDraft(BaseModel):
    """RCA LangGraph Reporter 节点产出；供 Reporter Agent 消费。"""

    summary: str = ""
    root_cause: str = ""
    recommendations: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    evidence_count: int = 0
    defect_type: str | None = None


class Report8dRequest(BaseModel):
    session_id: str
    root_cause: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    hitl_approved: bool = True
    confidence: float | None = None
    defect_type: str | None = None
    factory_id: str | None = None
    recommendations: list[str] = Field(default_factory=list)
    rca_artifacts: RcaArtifactDraft | dict[str, Any] | None = None


class Report8dResponse(BaseModel):
    report_md: str
    recommendations: list[str] = Field(default_factory=list)
    stub: bool = True
    capa_id: str | None = None
    qms_status: str | None = None
    generation_mode: Literal["deep_agent", "template"] = "template"


# ----- RCA 入参（Router 组装） -----


class RcaInvokeRequest(BaseModel):
    """Router 调 RCA OpenAPI 时由 Context 组装的稳定形状。"""

    session_id: str
    user_query: str
    batch_id: str | None = None
    defect_type: str | None = None
    prior_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    prior_evidence: list[dict[str, Any]] = Field(default_factory=list)


class RcaHitlResolveRequest(BaseModel):
    """Client / Router 调 RCA HITL 签核续跑。"""

    thread_id: str
    feedback: dict[str, Any] = Field(default_factory=dict)
