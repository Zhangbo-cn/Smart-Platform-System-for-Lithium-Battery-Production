"""A2A ↔ HITL 格式互转 & 状态映射。"""

from __future__ import annotations

from typing import Any

from platform_contracts.task_state import TaskState


def a2a_feedback_to_hitl(feedback: dict[str, Any]) -> dict[str, Any]:
    """将 A2A resume feedback 转为 HITL 内部格式。"""
    approved = bool(feedback.get("approved", feedback.get("action") == "approve"))
    hitl: dict[str, Any] = {
        "approved": approved,
        "feedback": str(feedback.get("feedback") or feedback.get("comment", "")),
    }
    if feedback.get("root_cause") or feedback.get("selected_root_cause"):
        hitl["root_cause"] = feedback.get("root_cause") or feedback.get("selected_root_cause")
    if feedback.get("recommendations"):
        hitl["recommendations"] = feedback.get("recommendations")
    if feedback.get("reviewer_id"):
        hitl["reviewer_id"] = feedback["reviewer_id"]
    return hitl


def analysis_status_to_task_state(status: str) -> str:
    """将 AnalysisResponse.status 映射为 TaskState。"""
    mapping: dict[str, str] = {
        "done": TaskState.COMPLETED,
        "completed": TaskState.COMPLETED,
        "hitl": TaskState.INPUT_REQUIRED,
        "input_required": TaskState.INPUT_REQUIRED,
        "failed": TaskState.FAILED,
        "running": TaskState.RUNNING,
    }
    return mapping.get(status, TaskState.RUNNING)
