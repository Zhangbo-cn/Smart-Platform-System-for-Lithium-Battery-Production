"""MemoryHarness 上下文自动压缩测试。"""

from __future__ import annotations

import pytest

from harness.context.memory_harness import MemoryHarness, _MAX_TURNS, _TURNS_AFTER_COMPRESS
from harness.memory.in_memory import InMemorySTM, InMemoryWorking, InMemoryLTM


@pytest.fixture
def memory():
    """创建使用 InMemory 后端的 MemoryHarness。"""
    stm = InMemorySTM(agent_name="test")
    working = InMemoryWorking()
    ltm = InMemoryLTM()
    return MemoryHarness(stm=stm, working=working, ltm=ltm)


@pytest.fixture
async def populated_memory(memory):
    """填入超过阈值数量的对话轮次。"""
    for i in range(_MAX_TURNS + 5):
        await memory.stm.append_turn("sess_1", "user" if i % 2 == 0 else "assistant",
                                      f"对话内容第{i+1}条")
    return memory


class TestAutoCompress:
    @pytest.mark.asyncio
    async def test_no_compress_below_threshold(self, memory):
        """轮数低于阈值时不触发压缩。"""
        for i in range(5):
            await memory.stm.append_turn("sess_2", "user", f"msg_{i}")
        result = await memory.compress_chat_context("sess_2")
        assert result is False  # 未压缩

    @pytest.mark.asyncio
    async def test_compress_above_threshold(self, populated_memory):
        """超过阈值时触发压缩。"""
        result = await populated_memory.compress_chat_context("sess_1")
        assert result is True  # 已压缩

    @pytest.mark.asyncio
    async def test_compress_reduces_turns(self, populated_memory):
        """压缩后轮数减少。"""
        before = len(await populated_memory.stm.get_turns("sess_1", last_n=999))
        assert before > _MAX_TURNS

        await populated_memory.compress_chat_context("sess_1")
        after = len(await populated_memory.stm.get_turns("sess_1", last_n=999))

        assert after == _TURNS_AFTER_COMPRESS  # 截断法保留最近轮次

    @pytest.mark.asyncio
    async def test_compress_with_llm(self, memory):
        """LLM 压缩模式下，摘要被作为 system 消息追加。"""
        call_count = 0

        def mock_llm(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"压缩摘要: {len(text)} chars"

        memory.llm_compress = mock_llm
        for i in range(_MAX_TURNS + 5):
            await memory.stm.append_turn("sess_3", "user", f"data_{i}")

        result = await memory.compress_chat_context("sess_3")
        assert result is True
        assert call_count >= 1

        turns = await memory.stm.get_turns("sess_3", last_n=999)
        # 应有 system 消息包含摘要
        assert any("压缩摘要" in t["content"] for t in turns)

    @pytest.mark.asyncio
    async def test_compress_in_build_planner_context(self, populated_memory):
        """build_planner_context 自动触发压缩。"""
        ctx = await populated_memory.build_planner_context(
            session_id="sess_1", user_id="u1", query="分析",
        )
        # 压缩后内容应被截断，不再包含早期消息
        assert "对话内容第1条" not in ctx
        assert "近期对话" in ctx  # 仍然有对话部分

    @pytest.mark.asyncio
    async def test_idempotent_compress(self, populated_memory):
        """重复压缩不会报错。"""
        await populated_memory.compress_chat_context("sess_1")
        await populated_memory.compress_chat_context("sess_1")  # 第二次应该无操作
        turns = await populated_memory.stm.get_turns("sess_1", last_n=999)
        assert len(turns) == _TURNS_AFTER_COMPRESS  # 保持不变
