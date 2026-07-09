"""PlaybookEngine → DAGEngine 迁移测试。"""

from __future__ import annotations

import os
import tempfile

import pytest
import yaml

from harness_core.dag_engine import DAGEngine


_OLD_YAML = """
playbooks:
  trace_only:
    description: 批次查询
    steps:
      - step: trace
        agent: trace-worker
        required: true
        context_write:
          prior_evidence: response.evidence

  rca:
    description: 单点根因分析
    steps:
      - step: trace
        agent: trace-worker
        condition: batch_id
        context_write:
          prior_evidence: response.evidence
      - step: rca
        agent: quality-rca-agent
        required: true
        context_write:
          rca.root_cause: response.root_cause
          rca.confidence: response.confidence
        hitl_check:
          field: response.requires_hitl

  investigate:
    description: 深度分析
    steps:
      - step: triage
        agent: triage-agent
        condition: not skip_triage and not defect_type
        context_write:
          defect_type: result.defect_type
      - step: trace
        agent: trace-worker
        condition: batch_id
      - step: rca
        agent: quality-rca-agent
        required: true
        hitl_check:
          field: response.requires_hitl
        fallback:
          on_empty: root_cause
          on_failure: true
"""


@pytest.fixture
def old_yaml_path():
    """创建临时旧格式 YAML 文件。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8",
    ) as f:
        f.write(_OLD_YAML)
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


class TestPlaybookMigration:
    """验证 DAGEngine 能解析旧格式 steps:。"""

    def test_parses_old_format_successfully(self, old_yaml_path):
        engine = DAGEngine.from_yaml(old_yaml_path)
        assert "trace_only" in engine.playbooks
        assert "rca" in engine.playbooks
        assert "investigate" in engine.playbooks

    def test_old_format_generates_dag_nodes(self, old_yaml_path):
        engine = DAGEngine.from_yaml(old_yaml_path)
        dag = engine.playbooks["rca"]
        assert "trace" in dag.nodes
        assert "rca" in dag.nodes

    def test_sequential_dependency(self, old_yaml_path):
        """旧格式步骤自动生成串行 depends_on 链。"""
        engine = DAGEngine.from_yaml(old_yaml_path)
        dag = engine.playbooks["rca"]
        assert dag.nodes["rca"].depends_on == ["trace"]

    def test_old_format_preserves_condition(self, old_yaml_path):
        engine = DAGEngine.from_yaml(old_yaml_path)
        dag = engine.playbooks["rca"]
        assert dag.nodes["trace"].condition == "batch_id"

    def test_old_format_preserves_hitl_check(self, old_yaml_path):
        engine = DAGEngine.from_yaml(old_yaml_path)
        dag = engine.playbooks["rca"]
        assert dag.nodes["rca"].hitl_check is not None
        assert dag.nodes["rca"].hitl_check["field"] == "response.requires_hitl"

    def test_old_format_preserves_fallback(self, old_yaml_path):
        engine = DAGEngine.from_yaml(old_yaml_path)
        dag = engine.playbooks["investigate"]
        assert dag.nodes["rca"].fallback is not None
        assert dag.nodes["rca"].fallback.get("on_failure") is True

    def test_context_write_preserved(self, old_yaml_path):
        engine = DAGEngine.from_yaml(old_yaml_path)
        dag = engine.playbooks["trace_only"]
        assert dag.nodes["trace"].context_write.get("prior_evidence") == "response.evidence"

    def test_compound_condition(self, old_yaml_path):
        """复合条件 'not skip_triage and not defect_type' 被保留并正确解析。"""
        engine = DAGEngine.from_yaml(old_yaml_path)
        dag = engine.playbooks["investigate"]
        assert dag.nodes["triage"].condition == "not skip_triage and not defect_type"
        # 条件表达式解析验证：无 skip_triage 且无 defect_type 时应执行
        ctx = {"defect_type": "low_capacity"}
        req = {"skip_triage": True}
        assert not engine._eval_condition(dag.nodes["triage"].condition, ctx, req)

    def test_compat_list_parse_matches_dict_parse(self, old_yaml_path):
        """旧格式解析结果与新格式逻辑上等价。"""
        import tempfile

        engine_via_steps = DAGEngine.from_yaml(old_yaml_path)
        dag_steps = engine_via_steps.playbooks["rca"]

        # 构建等价的新格式 YAML
        new_yaml = yaml.safe_load(_OLD_YAML)
        for pb in new_yaml["playbooks"].values():
            steps = pb.pop("steps", [])
            nodes = {}
            prev = None
            for s in steps:
                nid = s["step"]
                s.pop("step")
                if prev:
                    s["depends_on"] = [prev]
                nodes[nid] = s
                prev = nid
            pb["nodes"] = nodes

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as f:
            yaml.dump(new_yaml, f, allow_unicode=True)
            new_path = f.name

        engine_via_nodes = DAGEngine.from_yaml(new_path)
        dag_nodes = engine_via_nodes.playbooks["rca"]

        assert dag_steps.nodes["trace"].agent == dag_nodes.nodes["trace"].agent
        assert dag_steps.nodes["rca"].depends_on == dag_nodes.nodes["rca"].depends_on
        assert dag_steps.nodes["rca"].hitl_check == dag_nodes.nodes["rca"].hitl_check

        os.unlink(new_path)

    def test_new_format_still_works(self):
        """新格式 nodes: 仍然优先于旧格式 steps:。"""
        import tempfile

        mixed_yaml = """
playbooks:
  mixed:
    nodes:
      step_a:
        agent: agent-x
        parallel: true
    steps:
      - step: step_b
        agent: agent-y
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as f:
            f.write(mixed_yaml)
            path = f.name

        engine = DAGEngine.from_yaml(path)
        dag = engine.playbooks["mixed"]
        # 应优先解析 nodes 而非 steps
        assert "step_a" in dag.nodes
        assert "step_b" not in dag.nodes  # nodes 优先，steps 被忽略

        os.unlink(path)
