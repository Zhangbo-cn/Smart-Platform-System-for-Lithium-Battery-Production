"""Planner 输出契约：playbook + params，交 Orchestrator 执行。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PlaybookId = Literal[
    "investigate",
    "trace_only",
    "rca",
    "close_loop",
    "shift_patrol",
    "coating_incident",
    "pm_alert",
]


class PlannerRequest(BaseModel):
    message: str
    playbook: str | None = None
    batch_id: str | None = None
    factory_id: str | None = None
    defect_type: str | None = None
    session_id: str | None = None


class PlanParams(BaseModel):
    message: str = ""
    batch_id: str | None = None
    factory_id: str | None = None
    defect_type: str | None = None
    confirm_rca: bool = False
    skip_triage: bool = False
    hitl_approved: bool = False


class PlanResult(BaseModel):
    playbook: PlaybookId
    params: PlanParams
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    reasoning: str = ""

    def to_dispatch(self, session_id: str | None = None) -> dict:
        out: dict = {
            "playbook": self.playbook,
            "message": self.params.message,
            "batch_id": self.params.batch_id,
            "factory_id": self.params.factory_id,
            "defect_type": self.params.defect_type,
            "confirm_rca": self.params.confirm_rca,
            "skip_triage": self.params.skip_triage,
            "hitl_approved": self.params.hitl_approved,
        }
        if session_id:
            out["session_id"] = session_id
        return {k: v for k, v in out.items() if v is not None}
