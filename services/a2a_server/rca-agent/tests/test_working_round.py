"""Working Memory 轮次记录测试。"""

from __future__ import annotations

import pytest

from harness.context.memory_harness import MemoryHarness
from harness.memory.in_memory import InMemorySTM, InMemoryWorking, InMemoryLTM


@pytest.fixture
def harness():
    stm = InMemorySTM(agent_name="test")
    working = InMemoryWorking()
    ltm = InMemoryLTM()
    return MemoryHarness(stm=stm, working=working, ltm=ltm)


class TestRoundRecord:
    @pytest.mark.asyncio
    async def test_save_and_retrieve_round(self, harness):
        state = {
            "root_cause": "涂布温度异常",
            "confidence": 0.85,
            "defect_type": "low_capacity",
            "trace_id": "trc_test",
            "status": "done",
            "tool_calls": [
                {"tool": "mes.query_batch_trace", "duration_ms": 120, "error": None,
                 "result": {"data": "..."}},
                {"tool": "scada.query_equipment_timeseries", "duration_ms": 95, "error": None,
                 "result": {"values": [38, 42]}},
            ],
            "evidence": [
                {"description": "涂布温度42.3°C超出上限39°C", "confidence": 0.9},
            ],
        }
        await harness.persist_analysis("sess_1", "u1", "容量偏低原因", state)

        rounds = await harness.working.get_rounds("sess_1")
        assert len(rounds) == 1
        r = rounds[0]
        assert r["round_id"] == 1
        assert r["output_data"]["root_cause"] == "涂布温度异常"
        assert r["input_data"]["query"] == "容量偏低原因"

    @pytest.mark.asyncio
    async def test_tool_calls_compressed(self, harness):
        state = {
            "root_cause": "温度异常",
            "confidence": 0.8,
            "status": "done",
            "tool_calls": [
                {"tool": "mes.query_batch_trace", "duration_ms": 120, "error": None,
                 "result": [{"station": "涂布", "time": "08:15"}]},
                {"tool": "erp.query_material_batch", "duration_ms": 65, "error": None,
                 "result": {"supplier": "ABC"}},
            ],
            "evidence": [],
        }
        await harness.persist_analysis("sess_2", "u1", "test", state)
        rounds = await harness.working.get_rounds("sess_2")
        r = rounds[0]
        # 压缩后：只有 tool 名、耗时、成功状态，没有原始数据
        for tc in r["tool_calls"]:
            assert "tool" in tc
            assert "duration_ms" in tc
            assert "success" in tc
            assert "result" not in tc  # 原始数据被压缩掉了

    @pytest.mark.asyncio
    async def test_round_id_increments(self, harness):
        for i in range(3):
            state = {"root_cause": f"cause_{i}", "confidence": 0.5, "status": "done",
                     "tool_calls": [], "evidence": []}
            await harness.persist_analysis("sess_3", "u1", f"q_{i}", state)

        rounds = await harness.working.get_rounds("sess_3")
        assert len(rounds) == 3
        assert rounds[0]["round_id"] == 1
        assert rounds[1]["round_id"] == 2
        assert rounds[2]["round_id"] == 3

    @pytest.mark.asyncio
    async def test_token_usage_tracked(self, harness):
        state = {
            "root_cause": "test", "confidence": 0.5, "status": "done",
            "tool_calls": [], "evidence": [],
            "token_usage": {"input": 2100, "output": 450},
        }
        await harness.persist_analysis("sess_4", "u1", "q", state)
        rounds = await harness.working.get_rounds("sess_4")
        tu = rounds[0]["token_usage"]
        assert tu["input_tokens"] == 2100
        assert tu["output_tokens"] == 450

    @pytest.mark.asyncio
    async def test_key_findings_from_evidence(self, harness):
        state = {
            "root_cause": "涂布温度异常",
            "confidence": 0.85, "status": "done",
            "tool_calls": [],
            "evidence": [
                {"description": "涂布温度42.3°C超出上限39°C", "confidence": 0.9},
                {"description": "容量均值98.2%低于标准99.5%", "confidence": 0.85},
            ],
        }
        await harness.persist_analysis("sess_5", "u1", "q", state)
        rounds = await harness.working.get_rounds("sess_5")
        findings = rounds[0]["key_findings"]
        assert len(findings) == 2
        assert any("42.3" in f for f in findings)
        assert any("98.2" in f for f in findings)

    @pytest.mark.asyncio
    async def test_no_errors_with_empty_state(self, harness):
        """空 state 不抛出异常。"""
        await harness.persist_analysis("sess_6", "u1", "q", {})
        rounds = await harness.working.get_rounds("sess_6")
        assert len(rounds) == 1  # 仍然保存了一轮，内容为空
