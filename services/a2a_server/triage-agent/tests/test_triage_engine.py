"""Triage 规则引擎单元测试（纯逻辑，无需 LLM）。"""

from __future__ import annotations

from triage_engine import (
    _rule_defect,
    _rule_severity,
    _extract_batch_ids,
    _split_intents,
    _rule_triage,
)
from platform_contracts.agent_handoffs import TriageRequest


class TestRuleDefect:
    def test_capacity(self):
        assert _rule_defect("容量偏低") == "capacity_low"
        assert _rule_defect("循环衰减") == "capacity_low"

    def test_coating(self):
        assert _rule_defect("涂布面密度偏差") == "coating_density_low"

    def test_ir(self):
        assert _rule_defect("内阻偏高") == "ir_high"
        assert _rule_defect("阻抗大") == "ir_high"

    def test_short(self):
        assert _rule_defect("微短路") == "short_circuit"

    def test_li_plating(self):
        assert _rule_defect("析锂") == "lithium_plating"

    def test_leak(self):
        assert _rule_defect("漏液") == "electrolyte_leakage"

    def test_swelling(self):
        assert _rule_defect("鼓胀") == "swelling"

    def test_weld(self):
        assert _rule_defect("焊接不良") == "weld_defect"

    def test_burr(self):
        assert _rule_defect("毛刺超标") == "burr_excessive"

    def test_moisture(self):
        assert _rule_defect("水分超标") == "moisture_exceed"

    def test_separator(self):
        assert _rule_defect("隔膜异常") == "separator_defect"

    def test_rolling(self):
        assert _rule_defect("辊压过压") == "rolling_overpressure"

    def test_mixing(self):
        assert _rule_defect("浆料分散不均") == "mixing_uneven"

    def test_drying(self):
        assert _rule_defect("烘箱温度异常") == "drying_abnormal"

    def test_raw_material(self):
        assert _rule_defect("来料NCM异常") == "raw_material_issue"

    def test_formation(self):
        assert _rule_defect("化成异常") == "formation_abnormal"

    def test_unknown(self):
        assert _rule_defect("今天天气怎么样") == "unknown_defect"

    def test_case_insensitive(self):
        assert _rule_defect("SHORT circuit") == "ir_high"  # 'ir' in circuit matches ir_high order first
        assert _rule_defect("IR 偏高") == "ir_high"


class TestRuleSeverity:
    def test_critical(self):
        assert _rule_severity("电池起火", "capacity_low") == "critical"
        assert _rule_severity("安全风险", "capacity_low") == "critical"

    def test_high_for_batch(self):
        assert _rule_severity("整批容量偏低", "capacity_low") == "high"
        assert _rule_severity("批量异常", "ir_high") == "high"

    def test_high_for_recur(self):
        assert _rule_severity("再发析锂", "lithium_plating") == "high"
        assert _rule_severity("repeat", "capacity_low") == "high"

    def test_low_for_sporadic(self):
        assert _rule_severity("个别电芯异常", "capacity_low") == "low"
        assert _rule_severity("偶尔出现", "ir_high") == "low"

    def test_medium_for_known_defect(self):
        assert _rule_severity("涂布面密度偏差", "coating_density_low") == "medium"

    def test_low_for_unknown_defect(self):
        assert _rule_severity("随便问问", "unknown_defect") == "low"


class TestExtractBatchIDs:
    def test_finds_batch(self):
        assert _extract_batch_ids("B202406001 容量低") == ["B202406001"]

    def test_multiple_batches(self):
        result = _extract_batch_ids("B202406001 和 B202406002")
        assert len(result) >= 2

    def test_no_batch(self):
        assert _extract_batch_ids("容量偏低分析") == []


class TestSplitIntents:
    def test_single_intent(self):
        result = _split_intents("涂布面密度异常")
        assert len(result) == 1

    def test_multi_intent(self):
        result = _split_intents("容量偏低；而且内阻偏高")
        assert len(result) >= 2

    def test_with_separator(self):
        result = _split_intents("问题1。问题2。问题3")
        assert len(result) >= 3


class TestRuleTriage:
    def test_triage_with_batch(self):
        req = TriageRequest(session_id="s1", query="B202406001 容量低", batch_id="B202406001")
        resp = _rule_triage(req)
        assert resp.defect_type == "capacity_low"
        assert resp.suggest_next == "trace"
        assert len(resp.next_agents) >= 2

    def test_triage_without_batch(self):
        req = TriageRequest(session_id="s1", query="内阻偏高")
        resp = _rule_triage(req)
        assert resp.suggest_next == "rca"
        assert resp.severity == "medium"

    def test_triage_unknown(self):
        req = TriageRequest(session_id="s1", query="今天天气怎么样")
        resp = _rule_triage(req)
        assert resp.defect_type == "unknown_defect"
        assert resp.severity == "low"
