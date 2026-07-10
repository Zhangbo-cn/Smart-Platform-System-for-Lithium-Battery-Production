"""HITL interrupt/resume + MemoryHarness 测试。"""

from __future__ import annotations

import asyncio
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from harness.context.memory_harness import MemoryHarness
from harness.hitl.resume import (
    graph_config,
    interrupt_payload,
    is_interrupted,
    resume_command,
)
from harness.memory.in_memory import InMemorySTM
from harness.memory.long_term import LongTermMemory


class _S(TypedDict, total=False):
    trace_id: str
    user_query: str
    requires_hitl: bool
    partial_result: dict
    confidence: float
    hitl_response: dict
    root_cause: str
    status: str


async def _reflector_degrade(state: _S) -> dict:
    return {
        "requires_hitl": True,
        "partial_result": {"suspected": ["涂布厚度异常"], "excluded": ["注液量"]},
        "confidence": 0.55,
        "status": "hitl",
    }


async def _hitl_node(state: _S) -> dict:
    payload = {
        "trace_id": state.get("trace_id"),
        "confidence": state.get("confidence", 0.0),
        "partial_result": state.get("partial_result"),
    }
    feedback = interrupt(payload)
    updates: dict = {
        "hitl_response": feedback,
        "requires_hitl": False,
        "status": "reporting",
    }
    if isinstance(feedback, dict) and feedback.get("root_cause"):
        updates["root_cause"] = feedback["root_cause"]
    return updates


async def _reporter(state: _S) -> dict:
    return {
        "root_cause": state.get("root_cause", "未确认"),
        "final_report": "# report",
        "status": "done",
    }


def _build_test_graph():
    g = StateGraph(_S)

    def route(state: _S) -> str:
        if state.get("requires_hitl"):
            return "hitl"
        return "reporter"

    g.add_node("reflector", _reflector_degrade)
    g.add_node("hitl", _hitl_node)
    g.add_node("reporter", _reporter)
    g.add_edge(START, "reflector")
    g.add_conditional_edges("reflector", route, {"hitl": "hitl", "reporter": "reporter"})
    g.add_edge("hitl", "reporter")
    g.add_edge("reporter", END)
    return g.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_interrupt_resume_with_checkpoint():
    graph = _build_test_graph()
    cfg = graph_config("session-abc")

    paused = await graph.ainvoke(
        {"trace_id": "t1", "user_query": "容量偏低"},
        config=cfg,
    )
    assert is_interrupted(paused)
    payload = interrupt_payload(paused)
    assert payload["confidence"] == 0.55
    assert "涂布厚度异常" in payload["partial_result"]["suspected"]

    resumed = await graph.ainvoke(
        resume_command({"approved": True, "root_cause": "涂布厚度偏低"}),
        config=cfg,
    )
    assert not is_interrupted(resumed)
    assert resumed["status"] == "done"
    assert resumed["root_cause"] == "涂布厚度偏低"
    assert resumed["hitl_response"]["approved"] is True


@pytest.mark.asyncio
async def test_memory_harness_builds_layered_context():
    harness = MemoryHarness(
        stm=InMemorySTM(),
        working=None,
        ltm=LongTermMemory(),
    )
    session = "sess-1"
    await harness.stm.append_turn(session, "user", "上次容量偏低")
    await harness.stm.append_turn(session, "assistant", "根因：涂布厚度偏低")
    await harness.stm.set(
        session,
        {"root_cause": "涂布厚度偏低", "confidence": 0.82},
        slot="last_state",
    )

    ctx = await harness.build_planner_context(session, "u1", "内阻偏高")
    assert "近期对话" in ctx
    assert "上轮分析摘要" in ctx
    assert "涂布厚度偏低" in ctx


@pytest.mark.asyncio
async def test_memory_harness_persist_short_term():
    harness = MemoryHarness(
        stm=InMemorySTM(),
        working=None,
        ltm=LongTermMemory(),
    )
    session = "sess-2"
    await harness.persist_analysis(
        session,
        "u1",
        "析锂风险",
        {
            "trace_id": "tr-1",
            "root_cause": "负极析锂",
            "confidence": 0.9,
            "status": "done",
            "defect_type": "析锂",
        },
    )
    turns = await harness.stm.get_turns(session)
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    last = await harness.stm.get(session, slot="last_state")
    assert last["root_cause"] == "负极析锂"
