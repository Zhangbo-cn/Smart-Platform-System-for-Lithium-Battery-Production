"""Reporter 模块单测。"""

from __future__ import annotations

import pytest

from eval_core.judge import rule_judge_reporter_d4_locked
from platform_contracts.agent_handoffs import Report8dRequest, RcaArtifactDraft


def test_report8d_request_extended_fields():
    req = Report8dRequest(
        session_id="s1",
        root_cause="涂布 → N/P 比偏低",
        hitl_approved=True,
        defect_type="lithium_plating",
        factory_id="FD-01",
        recommendations=["复核涂布参数"],
        rca_artifacts=RcaArtifactDraft(summary="摘要", root_cause="涂布 → N/P 比偏低"),
    )
    assert req.factory_id == "FD-01"
    assert req.rca_artifacts.root_cause == req.root_cause


def test_d4_locked_judge():
    locked = "涂布 → N/P 比偏低"
    md = "# 8D\n\n## D4 根因\n涂布 → N/P 比偏低\n"
    v = rule_judge_reporter_d4_locked(md, locked)
    assert v.passed is True


def test_d4_locked_judge_fails_on_rewrite():
    v = rule_judge_reporter_d4_locked("根因：完全不同的结论", "涂布 → N/P 比偏低")
    assert v.passed is False
