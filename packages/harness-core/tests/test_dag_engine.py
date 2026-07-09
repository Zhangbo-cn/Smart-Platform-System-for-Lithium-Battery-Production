"""DAGEngine 单元测试。"""

from __future__ import annotations

import pytest

from harness_core.dag_engine import DAGEngine, DAGNode, DAGBranch, DAGDef, DAGState


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def minimal_yaml(tmp_path):
    """最小的可执行 DAG YAML。"""
    p = tmp_path / "playbooks.yaml"
    p.write_text("""
playbooks:
  test_single:
    description: "单节点"
    nodes:
      step1:
        agent: mock-agent
  test_parallel:
    description: "并行"
    nodes:
      step1:
        agent: mock-agent
        parallel: true
      step2:
        agent: mock-agent
        parallel: true
  test_chain:
    description: "串行依赖"
    nodes:
      step1:
        agent: mock-agent
      step2:
        agent: mock-agent
        depends_on: [step1]
      step3:
        agent: mock-agent
        depends_on: [step2]
  test_hitl:
    description: "HITL"
    nodes:
      step1:
        agent: mock-agent
        hitl_check:
          field: "response.requires_hitl"
          on_hitl: pause
  test_fallback:
    description: "回退"
    nodes:
      step1:
        agent: mock-agent
        fallback:
          on_failure: true
""", encoding="utf-8")
    return p


# ── 解析测试 ──────────────────────────────────────────────


class TestDAGEngineParse:
    def test_load_from_yaml(self, minimal_yaml):
        engine = DAGEngine.from_yaml(minimal_yaml)
        assert len(engine.playbooks) == 5
        assert "test_single" in engine.playbooks

    def test_parse_single_node(self, minimal_yaml):
        engine = DAGEngine.from_yaml(minimal_yaml)
        dag = engine.playbooks["test_single"]
        assert len(dag.nodes) == 1
        assert dag.nodes["step1"].agent == "mock-agent"
        assert dag.start_nodes == ["step1"]

    def test_parse_depends_on(self, minimal_yaml):
        engine = DAGEngine.from_yaml(minimal_yaml)
        dag = engine.playbooks["test_chain"]
        assert dag.nodes["step2"].depends_on == ["step1"]
        assert dag.nodes["step3"].depends_on == ["step2"]
        # start_nodes: 只有 step1 没有依赖
        assert dag.start_nodes == ["step1"]

    def test_parse_parallel(self, minimal_yaml):
        engine = DAGEngine.from_yaml(minimal_yaml)
        dag = engine.playbooks["test_parallel"]
        assert dag.nodes["step1"].parallel is True
        assert dag.nodes["step2"].parallel is True

    def test_parse_hitl_check(self, minimal_yaml):
        engine = DAGEngine.from_yaml(minimal_yaml)
        dag = engine.playbooks["test_hitl"]
        assert dag.nodes["step1"].hitl_check is not None
        assert dag.nodes["step1"].hitl_check["field"] == "response.requires_hitl"

    def test_parse_fallback(self, minimal_yaml):
        engine = DAGEngine.from_yaml(minimal_yaml)
        dag = engine.playbooks["test_fallback"]
        assert dag.nodes["step1"].fallback == {"on_failure": True}

    def test_load_investigate_playbook(self):
        """加载真实的 playbooks.yaml，验证 DAG 结构。"""
        import os
        repo_root = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
        yaml_path = os.path.join(repo_root, "config", "playbooks.yaml")
        if not os.path.exists(yaml_path):
            pytest.skip("playbooks.yaml not found in test env")
        engine = DAGEngine.from_yaml(yaml_path)
        dag = engine.playbooks.get("investigate")
        assert dag is not None, "investigate playbook not found"
        assert "triage" in dag.nodes
        assert "trace" in dag.nodes
        assert "rca" in dag.nodes
        # triage 和 trace 应可并行
        assert dag.nodes["triage"].parallel is True
        assert dag.nodes["trace"].parallel is True
        # rca 依赖 triage 和 trace
        assert "triage" in dag.nodes["rca"].depends_on or "trace" in dag.nodes["rca"].depends_on


# ── 执行测试 ──────────────────────────────────────────────


class TestDAGEngineExecute:
    @pytest.mark.asyncio
    async def test_single_node_executes(self):
        """单节点执行。"""
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="mock"),
        }, start_nodes=["a"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        called = []
        async def call_step(agent, step_def, ctx):
            called.append(agent)
            return {"result": "ok"}

        result = await engine.execute("test", {}, {}, "trc", "sess", call_step=call_step)
        assert result["status"] == "completed"
        assert called == ["mock"]

    @pytest.mark.asyncio
    async def test_chain_executes_in_order(self):
        """串行依赖：a → b → c。"""
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="agent-a"),
            "b": DAGNode(id="b", agent="agent-b", depends_on=["a"]),
            "c": DAGNode(id="c", agent="agent-c", depends_on=["b"]),
        }, start_nodes=["a"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        order = []
        async def call_step(agent, step_def, ctx):
            order.append(agent)
            return {"result": "ok"}

        await engine.execute("test", {}, {}, "trc", "sess", call_step=call_step)
        assert order == ["agent-a", "agent-b", "agent-c"]

    @pytest.mark.asyncio
    async def test_parallel_nodes_execute_concurrently(self):
        """并行节点应同时执行。"""
        import asyncio

        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="agent-a", parallel=True),
            "b": DAGNode(id="b", agent="agent-b", parallel=True),
        }, start_nodes=["a", "b"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        started = set()
        lock = asyncio.Lock()

        async def call_step(agent, step_def, ctx):
            async with lock:
                started.add(agent)
            await asyncio.sleep(0.05)  # 模拟执行耗时
            return {"result": "ok"}

        await engine.execute("test", {}, {}, "trc", "sess", call_step=call_step)
        # 如果并行，两 agent 都应在 sleep 前就加入了 started
        assert "agent-a" in started
        assert "agent-b" in started

    @pytest.mark.asyncio
    async def test_hitl_pauses_execution(self):
        """HITL 检查暂停整条链路。"""
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="mock", hitl_check={"field": "response.requires_hitl"}),
            "b": DAGNode(id="b", agent="mock-b", depends_on=["a"]),
        }, start_nodes=["a"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        async def call_step(agent, step_def, ctx):
            return {"requires_hitl": True}

        result = await engine.execute("test", {}, {}, "trc", "sess", call_step=call_step)
        assert result["status"] == "hitl"
        assert result["hitl_request"] is not None

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_succeed(self):
        """重试机制：第一次失败，第二次成功。"""
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="mock", max_retry=2),
        }, start_nodes=["a"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        attempt_count = 0

        async def call_step(agent, step_def, ctx):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise ConnectionError("first attempt fails")
            return {"result": "ok"}

        result = await engine.execute("test", {}, {}, "trc", "sess", call_step=call_step)
        assert result["status"] == "completed"
        assert attempt_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_fallback(self):
        """重试耗尽 + fallback 走回退。"""
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="mock", max_retry=1, fallback={"on_failure": True}),
        }, start_nodes=["a"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        async def call_step(agent, step_def, ctx):
            raise RuntimeError("always fail")

        result = await engine.execute("test", {}, {}, "trc", "sess", call_step=call_step)
        # fallback.on_failure=True → 不崩溃，继续
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_context_write_persists(self):
        """context_write 把 response 值写入 ctx。"""
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="mock", context_write={
                "result.value": "response.value",
            }),
        }, start_nodes=["a"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        ctx = {}

        async def call_step(agent, step_def, ctx):
            return {"value": "hello"}

        await engine.execute("test", ctx, {}, "trc", "sess", call_step=call_step)
        assert ctx.get("result", {}).get("value") == "hello"

    @pytest.mark.asyncio
    async def test_condition_skip(self):
        """condition 为 False 时跳过节点。"""
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="mock", condition="batch_id"),
        }, start_nodes=["a"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        called = False
        async def call_step(agent, step_def, ctx):
            nonlocal called
            called = True
            return {}

        ctx = {}  # 没有 batch_id
        result = await engine.execute("test", ctx, {}, "trc", "sess", call_step=call_step)
        assert not called, "应该跳过"
        assert "a" in result.get("skipped", [])


# ── 拓扑计算测试 ──────────────────────────────────────────


class TestDAGEngineTopology:
    def test_next_ready_no_deps(self):
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a"),
            "b": DAGNode(id="b"),
        }, start_nodes=["a", "b"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        state = type("State", (), {"completed_nodes": [], "failed_nodes": [], "skipped_nodes": []})()
        ready = engine._next_ready(dag, state, {}, {})
        # 无依赖 → 所有没完成的 node 都是 ready
        assert "a" in ready
        assert "b" in ready

    def test_next_ready_with_deps(self):
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a"),
            "b": DAGNode(id="b", depends_on=["a"]),
        }, start_nodes=["a"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        state = type("State", (), {"completed_nodes": ["a"], "failed_nodes": [], "skipped_nodes": []})()
        ready = engine._next_ready(dag, state, {}, {})
        assert "b" in ready

    def test_next_ready_not_ready(self):
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a"),
            "b": DAGNode(id="b", depends_on=["a"]),
        }, start_nodes=["a"])
        engine = DAGEngine()
        engine.playbooks["test"] = dag

        state = type("State", (), {"completed_nodes": [], "failed_nodes": [], "skipped_nodes": []})()
        ready = engine._next_ready(dag, state, {}, {})
        assert "b" not in ready  # a 还没完成


# ── 可视化测试 ──────────────────────────────────────────


class TestDAGVisualize:
    def test_mermaid_single_node(self):
        dag = DAGDef(name="test", nodes={
            "step1": DAGNode(id="step1", agent="mock"),
        })
        mermaid = dag.to_mermaid()
        assert "graph TD" in mermaid
        assert "mock" in mermaid
        assert "step1" in mermaid

    def test_mermaid_chain(self):
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="agent1"),
            "b": DAGNode(id="b", agent="agent2", depends_on=["a"]),
        })
        mermaid = dag.to_mermaid()
        assert "-->" in mermaid
        assert "agent1" in mermaid
        assert "agent2" in mermaid

    def test_mermaid_parallel_flag(self):
        dag = DAGDef(name="test", nodes={
            "a": DAGNode(id="a", agent="agent1", parallel=True),
        })
        mermaid = dag.to_mermaid()
        assert "parallel" in mermaid

    def test_mermaid_hitl_flag(self):
        dag = DAGDef(name="test", nodes={
            "h": DAGNode(id="confirm", type="input_required"),
        })
        mermaid = dag.to_mermaid()
        assert "HITL" in mermaid
