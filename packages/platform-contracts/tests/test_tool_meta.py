"""ToolMeta 单元测试。"""

from __future__ import annotations

import pytest
from platform_contracts.tool_meta import (
    ToolMeta,
    ToolParam,
    PlanStep,
    ValidationResult,
    validate_plan_step,
    build_tool_schema_for_llm,
)


class TestToolMetaModel:
    def test_create_tool_meta(self):
        meta = ToolMeta(
            tool_name="query_batch",
            description="批次查询",
            params=[
                ToolParam(name="batch_id", type="string", required=True, description="批次号"),
                ToolParam(name="line", type="string", enum=["A", "B", "C"]),
            ],
        )
        assert meta.tool_name == "query_batch"
        assert len(meta.params) == 2
        assert meta.params[0].required is True

    def test_plan_step_defaults(self):
        step = PlanStep(step_id=1, tool="test", tool_args={})
        assert step.step_id == 1
        assert step.parallel is False
        assert step.rationale == ""

    def test_plan_step_parallel(self):
        step = PlanStep(step_id=2, tool="test", action="查数据", tool_args={"id": "1"}, parallel=True)
        assert step.parallel is True


class TestValidatePlanStep:
    def test_valid_step(self):
        meta = ToolMeta(
            tool_name="query",
            params=[
                ToolParam(name="id", type="string", required=True),
                ToolParam(name="type", type="string", enum=["a", "b"]),
            ],
        )
        step = PlanStep(step_id=1, tool="query", tool_args={"id": "123", "type": "a"})
        result = validate_plan_step(step, meta)
        assert result.valid
        assert len(result.errors) == 0

    def test_missing_required_param(self):
        meta = ToolMeta(
            tool_name="query",
            params=[
                ToolParam(name="id", type="string", required=True),
            ],
        )
        step = PlanStep(step_id=1, tool="query", tool_args={})
        result = validate_plan_step(step, meta)
        assert not result.valid
        assert any("必填" in e for e in result.errors)
        assert "id" in result.missing_params

    def test_enum_violation(self):
        meta = ToolMeta(
            tool_name="query",
            params=[
                ToolParam(name="type", type="string", enum=["a", "b"]),
            ],
        )
        step = PlanStep(step_id=1, tool="query", tool_args={"type": "c"})
        result = validate_plan_step(step, meta)
        assert not result.valid
        assert any("枚举" in e for e in result.errors)

    def test_type_mismatch(self):
        meta = ToolMeta(
            tool_name="query",
            params=[
                ToolParam(name="count", type="integer"),
            ],
        )
        step = PlanStep(step_id=1, tool="query", tool_args={"count": "not_a_number"})
        result = validate_plan_step(step, meta)
        assert not result.valid
        assert any("类型错误" in e for e in result.errors)

    def test_unknown_param_warns(self):
        meta = ToolMeta(tool_name="query", params=[])
        step = PlanStep(step_id=1, tool="query", tool_args={"unknown_key": "val"})
        result = validate_plan_step(step, meta)
        assert result.valid  # 未知参数不影响有效
        assert any("不在工具定义中" in e for e in result.warnings)

    def test_no_params_valid(self):
        meta = ToolMeta(tool_name="noop")
        step = PlanStep(step_id=1, tool="noop", tool_args={})
        result = validate_plan_step(step, meta)
        assert result.valid

    def test_float_type_check(self):
        meta = ToolMeta(
            tool_name="set_temp",
            params=[ToolParam(name="temp", type="float", required=True)],
        )
        step = PlanStep(step_id=1, tool="set_temp", tool_args={"temp": 38.5})
        assert validate_plan_step(step, meta).valid

        step2 = PlanStep(step_id=2, tool="set_temp", tool_args={"temp": "hot"})
        assert not validate_plan_step(step2, meta).valid


class TestBuildToolSchemaForLLM:
    def test_build_basic(self):
        tools = [
            ToolMeta(
                tool_name="query",
                description="查询工具",
                params=[
                    ToolParam(name="id", type="string", required=True),
                ],
            ),
        ]
        schemas = build_tool_schema_for_llm(tools)
        assert len(schemas) == 1
        fn = schemas[0]["function"]
        assert fn["name"] == "query"
        assert fn["description"] == "查询工具"
        assert "id" in fn["parameters"]["properties"]
        assert fn["parameters"]["required"] == ["id"]

    def test_build_with_enum(self):
        tools = [
            ToolMeta(
                tool_name="classify",
                params=[
                    ToolParam(name="type", type="string", enum=["A", "B"]),
                ],
            ),
        ]
        schemas = build_tool_schema_for_llm(tools)
        props = schemas[0]["function"]["parameters"]["properties"]
        assert props["type"]["enum"] == ["A", "B"]

    def test_build_empty_params(self):
        tools = [ToolMeta(tool_name="ping")]
        schemas = build_tool_schema_for_llm(tools)
        fn = schemas[0]["function"]
        assert fn["name"] == "ping"
        assert fn["parameters"]["required"] == []

    def test_build_multiple_tools(self):
        tools = [
            ToolMeta(tool_name="tool_a", params=[ToolParam(name="x", type="string")]),
            ToolMeta(tool_name="tool_b", params=[ToolParam(name="y", type="integer")]),
        ]
        schemas = build_tool_schema_for_llm(tools)
        assert len(schemas) == 2
        assert schemas[0]["function"]["name"] == "tool_a"
        assert schemas[1]["function"]["name"] == "tool_b"
