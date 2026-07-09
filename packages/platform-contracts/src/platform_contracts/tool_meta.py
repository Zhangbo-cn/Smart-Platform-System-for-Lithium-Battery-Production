"""工具元数据 Schema 定义 — 感知层/规划层共享。

用于：
  1. 感知层强制 LLM 输出匹配 Tool 参数 Schema
  2. 规划层校验 planner 输出是否合法
  3. 动态生成 LLM function calling schema
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolParam(BaseModel):
    """单个工具参数的定义。"""
    name: str
    type: Literal["string", "integer", "float", "boolean", "array", "object"]
    required: bool = False
    description: str = ""
    enum: list[str] | None = None
    default: Any = None
    example: Any = None


class ToolMeta(BaseModel):
    """工具的完整元数据描述。"""
    tool_name: str
    description: str = ""
    params: list[ToolParam] = Field(default_factory=list)
    sensitive: bool = False
    required_roles: list[str] = Field(default_factory=list)
    exclusive_agent: str | None = None


class PlanStep(BaseModel):
    """单步工具调用的结构化输出（planner 校验目标）。"""
    step_id: int = Field(ge=1)
    action: str = ""
    tool: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    parallel: bool = False


class ValidationResult(BaseModel):
    """参数校验结果。"""
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_params: list[str] = Field(default_factory=list)


def validate_plan_step(step: PlanStep, tool_meta: ToolMeta) -> ValidationResult:
    """校验单步计划是否匹配工具定义。

    Args:
        step: planner 输出的单步计划
        tool_meta: 工具的元数据定义

    Returns:
        ValidationResult: 校验结果
    """
    result = ValidationResult(valid=True)

    # 1. 必填参数检查
    for param in tool_meta.params:
        if param.required and param.name not in step.tool_args:
            result.valid = False
            result.errors.append(f"缺少必填参数 '{param.name}'")
            result.missing_params.append(param.name)

    # 2. 类型检查 + 枚举检查
    for key, value in step.tool_args.items():
        param_def = next((p for p in tool_meta.params if p.name == key), None)
        if param_def is None:
            result.warnings.append(f"参数 '{key}' 不在工具定义中，将被忽略")
            continue

        # 枚举值检查
        if param_def.enum and value not in param_def.enum:
            result.valid = False
            result.errors.append(
                f"参数 '{key}' 值 '{value}' 不在枚举范围 {param_def.enum} 内"
            )

        # 类型检查（基本）
        type_map = {
            "string": str,
            "integer": int,
            "float": (int, float),
            "boolean": bool,
        }
        expected_type = type_map.get(param_def.type)
        if expected_type and not isinstance(value, expected_type):
            result.valid = False
            result.errors.append(
                f"参数 '{key}' 类型错误：期望 {param_def.type}，实际 {type(value).__name__}"
            )

    return result


def build_tool_schema_for_llm(tools: list[ToolMeta]) -> list[dict]:
    """将 ToolMeta 列表转换为 LLM function calling schema。

    可用于 OpenAI/DeepSeek 的 tools 参数，也可嵌入 system prompt。
    """
    schemas = []
    for tool in tools:
        properties = {}
        required = []
        for p in tool.params:
            prop = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            if p.default is not None:
                prop["default"] = p.default
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        schemas.append({
            "type": "function",
            "function": {
                "name": tool.tool_name,
                "description": tool.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return schemas
