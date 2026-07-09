"""端到端集成测试：DAGEngine + A2A mock agent + 上下文传递。

不依赖外部服务，全进程内 mock。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import structlog

from harness_core.dag_engine import DAGEngine, DAGDef, DAGNode, DAGState

logger = structlog.get_logger(__name__)


# ── 测试场景 1: 链式 DAG ─────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_chain_dag():
    """串行链式 DAG: step1 → step2 → step3, 上下文传递。"""
    dag = DAGDef(name="e2e_chain", nodes={
        "triage": DAGNode(id="triage", agent="triage-agent",
                          context_write={"defect_type": "response.defect"}),
        "trace": DAGNode(id="trace", agent="trace-worker",
                          depends_on=["triage"],
                          context_write={"batch_id": "response.batch"}),
        "rca": DAGNode(id="rca", agent="quality-rca-agent",
                       depends_on=["trace"],
                       context_write={"root_cause": "response.root"}),
    }, start_nodes=["triage"])
    engine = DAGEngine()
    engine.playbooks["e2e_chain"] = dag

    ctx: dict[str, Any] = {}
    order = []

    async def call_step(agent: str, step_def: dict, _ctx: dict) -> dict:
        order.append(agent)
        if agent == "triage-agent":
            return {"defect": "low_capacity"}
        if agent == "trace-worker":
            return {"batch": "BATCH-001"}
        if agent == "quality-rca-agent":
            return {"root": "涂布温度异常"}
        return {}

    async def emit(_event, _step, _agent, _msg):
        pass

    result = await engine.execute(
        "e2e_chain", ctx, {}, "trc_e2e", "sess_e2e",
        call_step=call_step, emit_event=emit,
    )

    assert result["status"] == "completed"
    assert order == ["triage-agent", "trace-worker", "quality-rca-agent"]
    assert ctx.get("defect_type") == "low_capacity"
    assert ctx.get("batch_id") == "BATCH-001"
    assert ctx.get("root_cause") == "涂布温度异常"


# ── 测试场景 2: 并行 DAG ─────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_parallel_dag():
    """并行节点同时执行，结果汇聚到下游。"""
    dag = DAGDef(name="e2e_parallel", nodes={
        "source_a": DAGNode(id="source_a", agent="agent-a", parallel=True,
                            context_write={"data_a": "response.val"}),
        "source_b": DAGNode(id="source_b", agent="agent-b", parallel=True,
                            context_write={"data_b": "response.val"}),
        "merge": DAGNode(id="merge", agent="agent-merge",
                         depends_on=["source_a", "source_b"],
                         context_write={"merged": "response.val"}),
    }, start_nodes=["source_a", "source_b"])
    engine = DAGEngine()
    engine.playbooks["e2e_parallel"] = dag

    ctx: dict[str, Any] = {}
    order: list[str] = []

    async def call_step(agent: str, step_def: dict, _ctx: dict) -> dict:
        order.append(agent)
        return {"val": f"result_from_{agent}"}

    async def emit(_event, _step, _agent, _msg):
        pass

    result = await engine.execute(
        "e2e_parallel", ctx, {}, "trc_par", "sess_par",
        call_step=call_step, emit_event=emit,
    )

    assert result["status"] == "completed"
    assert "agent-a" in order and "agent-b" in order
    # source_a 和 source_b 都在 merge 之前执行
    assert order.index("agent-a") < order.index("agent-merge")
    assert order.index("agent-b") < order.index("agent-merge")
    assert ctx.get("data_a") == "result_from_agent-a"
    assert ctx.get("data_b") == "result_from_agent-b"
    assert ctx.get("merged") == "result_from_agent-merge"


# ── 测试场景 3: HITL 暂停与恢复 ──────────────────────────


@pytest.mark.asyncio
async def test_e2e_hitl_pause():
    """节点触发 HITL 后整条链路暂停，下游不执行。"""
    dag = DAGDef(name="e2e_hitl", nodes={
        "rca": DAGNode(id="rca", agent="quality-rca-agent",
                       hitl_check={"field": "response.requires_hitl"}),
        "report": DAGNode(id="report", agent="report-agent",
                          depends_on=["rca"]),
    }, start_nodes=["rca"])
    engine = DAGEngine()
    engine.playbooks["e2e_hitl"] = dag

    call_order = []

    async def call_step(agent: str, _step_def: dict, _ctx: dict) -> dict:
        call_order.append(agent)
        return {"requires_hitl": True, "root_cause": "需确认"}

    result = await engine.execute(
        "e2e_hitl", {}, {}, "trc_hitl", "sess_hitl",
        call_step=call_step,
    )

    assert result["status"] == "hitl"
    assert len(call_order) == 1  # report 没执行
    assert result["hitl_request"] is not None


# ── 测试场景 4: 空结果回退 ───────────────────────────────


@pytest.mark.asyncio
async def test_e2e_fallback_on_empty():
    """空根因时触发 fallback。"""
    dag = DAGDef(name="e2e_fb", nodes={
        "rca": DAGNode(id="rca", agent="quality-rca-agent",
                       fallback={"on_failure": True}),
    }, start_nodes=["rca"])
    engine = DAGEngine()
    engine.playbooks["e2e_fb"] = dag

    async def call_step(agent: str, _step_def: dict, _ctx: dict) -> dict:
        raise RuntimeError("LLM 超时")

    result = await engine.execute(
        "e2e_fb", {}, {}, "trc_fb", "sess_fb",
        call_step=call_step,
    )
    assert result["status"] == "completed"  # fallback 兜住


# ── 测试场景 5: 条件跳过 ────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_condition_skip():
    """条件不满足时跳过节点。"""
    dag = DAGDef(name="e2e_cond", nodes={
        "triage": DAGNode(id="triage", agent="triage-agent",
                          condition="not defect_type"),
        "rca": DAGNode(id="rca", agent="quality-rca-agent",
                       depends_on=["triage"]),
    }, start_nodes=["triage"])
    engine = DAGEngine()
    engine.playbooks["e2e_cond"] = dag

    call_order = []

    async def call_step(agent: str, _step_def: dict, _ctx: dict) -> dict:
        call_order.append(agent)
        return {}

    ctx = {"defect_type": "low_capacity"}  # 已提供 defect_type → triage 应跳过
    result = await engine.execute(
        "e2e_cond", ctx, {}, "trc_cond", "sess_cond",
        call_step=call_step,
    )

    assert "triage" in result.get("skipped", [])
    assert "triage-agent" not in call_order


# ── 测试场景 6: 输入确认（input_required）───────────────


@pytest.mark.asyncio
async def test_e2e_input_required():
    """input_required 节点暂停返回。"""
    dag = DAGDef(name="e2e_confirm", nodes={
        "check": DAGNode(id="check", type="input_required"),
        "next": DAGNode(id="next", agent="agent-x", depends_on=["check"]),
    }, start_nodes=["check"])
    engine = DAGEngine()
    engine.playbooks["e2e_confirm"] = dag

    result = await engine.execute("e2e_confirm", {}, {}, "trc_cfm", "sess_cfm")
    assert result["status"] == "awaiting_confirm"
    assert result["current_step"] == "check"


# ── 测试场景 7: 真实 playbooks.yaml 加载集成 ─────────────


def test_e2e_real_playbook_load():
    """加载正式 playbooks.yaml，验证所有 DAG 结构合法。"""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                        "config", "playbooks.yaml")
    if not os.path.exists(path):
        pytest.skip("playbooks.yaml not found")
    engine = DAGEngine.from_yaml(path)
    for name, dag in engine.playbooks.items():
        # 所有节点必须有 ID
        for nid, node in dag.nodes.items():
            assert node.id == nid
        # start_nodes 非空
        assert dag.start_nodes, f"{name}: no start nodes"
        # 无循环依赖（快速检查）
        visited = set()
        def check_cycle(nid, path):
            if nid in visited:
                return
            visited.add(nid)
            for dep in dag.nodes[nid].depends_on:
                if dep in path:
                    pytest.fail(f"Cycle detected: {dep}")
                check_cycle(dep, path + [dep])
        for sn in dag.start_nodes:
            check_cycle(sn, [sn])
    # 确认并行配置存在
    inv = engine.playbooks["investigate"]
    assert inv.nodes["triage"].parallel is True
    assert inv.nodes["trace"].parallel is True
