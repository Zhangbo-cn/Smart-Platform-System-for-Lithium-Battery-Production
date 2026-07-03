from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from platform_contracts.agent_handoffs import RcaInvokeRequest


class AnalysisRequest(RcaInvokeRequest):
    """对齐 platform-contracts RcaInvokeRequest；session_id 可选；保留 query 别名。"""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str | None = None
    user_query: str = Field(
        min_length=4,
        validation_alias=AliasChoices("user_query", "query"),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_body(cls, data: Any) -> Any:
        if isinstance(data, dict) and "user_query" not in data and "query" in data:
            data = {**data, "user_query": data["query"]}
        return data


class AnalysisResponse(BaseModel):
    trace_id: str
    thread_id: str
    status: str
    root_cause: str
    recommendations: list[str]
    confidence: float
    report_md: str
    requires_hitl: bool
    hitl_request_id: str | None = None
    hitl_payload: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    rca_artifacts: dict[str, Any] | None = None


class HITLResolveRequest(BaseModel):
    thread_id: str | None = None
    request_id: str | None = None
    approved: bool
    feedback: str = ""
    root_cause: str | None = None
    recommendations: list[str] | None = None
    extra: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _require_thread_or_request(self) -> HITLResolveRequest:
        if not self.thread_id and not self.request_id:
            raise ValueError("thread_id or request_id required")
        return self

