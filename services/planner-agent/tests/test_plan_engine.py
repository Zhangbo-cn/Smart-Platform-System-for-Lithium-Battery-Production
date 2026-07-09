"""Planner 规则引擎单元测试（纯逻辑，无需 LLM）。"""

from __future__ import annotations

from plan_engine import plan, list_playbooks, _extract_batch_id
from platform_contracts.plan_result import PlanResult, PlannerRequest


class TestBatchIdExtraction:
    def test_extract_batch_pattern(self):
        assert _extract_batch_id("查一下 B202406001 的原因", None) is not None

    def test_extract_batch_prefix(self):
        bid = _extract_batch_id("batch-20240701 容量低", None)
        assert bid is not None
        # 标准化处理

    def test_no_batch_in_message(self):
        assert _extract_batch_id("分析一下容量衰减", None) is None

    def test_given_batch_overrides(self):
        bid = _extract_batch_id("随便说说", "BATCH-001")
        assert bid == "BATCH-001"


class TestListPlaybooks:
    def test_returns_known_playbooks(self):
        pbs = list_playbooks()
        ids = [p["id"] for p in pbs]
        assert "investigate" in ids
        assert "trace_only" in ids
        assert "rca" in ids
        assert "close_loop" in ids

    def test_playbooks_have_description(self):
        for pb in list_playbooks():
            assert pb["description"]


class TestRuleBasedPlan:
    def test_trace_only_keyword(self):
        req = PlannerRequest(message="查批次 B202406001 的流转记录")
        result = plan(req)
        assert result.playbook == "trace_only"
        assert result.confidence >= 0.5

    def test_close_loop_keyword(self):
        req = PlannerRequest(message="出 8D 报告，闭环")
        result = plan(req)
        assert result.playbook == "close_loop"

    def test_rca_keyword_without_batch(self):
        req = PlannerRequest(message="帮我分析一下为什么容量偏低")
        result = plan(req)
        assert result.playbook == "rca"

    def test_investigate_with_batch(self):
        req = PlannerRequest(message="B202406001 容量偏低原因分析")
        result = plan(req)
        assert result.playbook in ("investigate", "rca")

    def test_unknown_query_defaults_to_investigate(self):
        req = PlannerRequest(message="今天天气怎么样")
        result = plan(req)
        assert result.playbook == "investigate"

    def test_given_playbook_is_respected(self):
        req = PlannerRequest(
            message="帮我查个东西",
            playbook="trace_only",
            batch_id="BATCH-001",
        )
        result = plan(req)
        assert result.playbook == "trace_only"

    def test_given_playbook_with_conflicting_keyword(self):
        """指定 playbook 优先于关键词匹配。"""
        req = PlannerRequest(
            message="我要查批次还要做 RCA",
            playbook="trace_only",
        )
        result = plan(req)
        assert result.playbook == "trace_only"

    def test_params_passed_through(self):
        req = PlannerRequest(
            message="分析原因",
            factory_id="factory-a",
            defect_type="low_capacity",
            batch_id="BATCH-001",
        )
        result = plan(req)
        assert result.params.factory_id == "factory-a"
        assert result.params.defect_type == "low_capacity"
        assert result.params.batch_id == "BATCH-001"

    def test_confidence_higher_with_batch(self):
        req_with = PlannerRequest(message="分析原因", batch_id="B001")
        req_without = PlannerRequest(message="分析原因")
        assert plan(req_with).confidence >= plan(req_without).confidence

    def test_confirm_rca_default_true_for_investigate(self):
        req = PlannerRequest(message="B001 容量低", batch_id="B001")
        result = plan(req)
        if result.playbook == "investigate":
            assert result.params.confirm_rca is True

    def test_capa_keyword_triggers_close_loop(self):
        req = PlannerRequest(message="需要出 CAPA 报告")
        result = plan(req)
        assert result.playbook == "close_loop"

    def test_trace_fallback_without_batch(self):
        """说'追溯'但没有批次号，仍走 trace_only。"""
        req = PlannerRequest(message="帮我追溯一下")
        result = plan(req)
        assert result.playbook == "trace_only"
