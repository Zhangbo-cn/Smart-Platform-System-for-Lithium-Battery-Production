from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from agent.agents.base import BaseAgent
from agent.llm import DataSensitivity, TaskDifficulty
from agent.state import PlanStep, QualityAnalysisState
from agent.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "planner_system.md"


class PlannerAgent(BaseAgent):
    name = "planner"

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        llm_client=None,
    ) -> None:
        super().__init__(llm_client=llm_client)
        self.registry = registry

    def _build_tool_list(self) -> str:
        """从 ToolRegistry 动态生成可用工具列表注入 prompt。"""
        if not self.registry:
            return ""
        tools = self.registry.list_tools()
        lines = ["## 可用工具清单"]
        for t in tools:
            params = t.get("input_schema", {}).get("properties", {})
            param_str = ", ".join(
                f"{n}: {p.get('type', 'any')}{'（必填）' if n in (t.get('input_schema', {}).get('required', []) or []) else ''}"
                for n, p in params.items()
            ) or "无参数"
            lines.append(f"  - `{t['name']}`: {t.get('description', '')}（{param_str}）")
        return "\n".join(lines)

    def _system_prompt(self) -> list[dict]:
        text = PROMPT_PATH.read_text(encoding="utf-8")
        tool_list = self._build_tool_list()
        if tool_list:
            text += f"\n\n{tool_list}"
        return [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}},
        ]

    def _validate_step(self, step: dict[str, Any]) -> list[str]:
        """校验单步计划：tool 名存在性 + 必填参数完整性。

        Returns:
            错误列表，空列表 = 校验通过
        """
        errors: list[str] = []
        if self.registry is None:
            return errors

        tool_name = step.get("tool", "")
        if not tool_name:
            return errors

        # 检查 tool 是否已注册
        all_tools = self.registry.list_tools()
        tool_def = next((t for t in all_tools if t["name"] == tool_name), None)
        if tool_def is None:
            errors.append(f"工具 '{tool_name}' 未注册（不在白名单中），将被跳过")
            return errors

        # 检查必填参数
        schema = tool_def.get("input_schema", {})
        required = schema.get("required", [])
        tool_args = step.get("tool_args", {})
        for param in required:
            if param not in tool_args:
                errors.append(f"工具 '{tool_name}' 缺少必填参数 '{param}'")

        return errors

    async def run(self, state: QualityAnalysisState) -> dict[str, Any]:
        user_query = state["user_query"]
        memory_context = state.get("memory_context", "")
        batch_id = state.get("batch_id") or ""
        context_block = f"{memory_context}\n\n" if memory_context else ""
        batch_block = f"目标批次 batch_id={batch_id}\n" if batch_id else ""
        resp = await self.call_llm(
            system=self._system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{context_block}{batch_block}"
                        f"用户问题：{user_query}\n\n"
                        "请输出 JSON 格式的分析步骤计划，字段如：\n"
                        '{"steps": [{"step_id": 1, "action": "...", "tool": "...", '
                        '"tool_args": {}, "rationale": "...", "parallel": true/false}]}\n\n'
                        "注意：\n"
                        "- 只能使用「可用工具清单」中的工具，不得编造\n"
                        "- 如果多个步骤之间没有数据依赖（如查询不同工序的数据），设置 parallel=true\n"
                        "- 例如：同时查询涂布、辊压、卷绕工序的数据可以并行\n"
                        "- 有依赖关系的步骤（如先查批次，再查明细）设置 parallel=false"
                    ),
                }
            ],
            difficulty=TaskDifficulty.MODERATE,
            sensitivity=DataSensitivity.LOW,
            caller="planner.run",
        )

        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        raw_plan: list[dict[str, Any]] = []
        try:
            raw_plan = json.loads(text)["steps"]
        except (json.JSONDecodeError, KeyError):
            logger.warning("planner.json_parse_failed", text=text[:200])

        # 逐步骤校验，过滤掉非法步骤
        validated: list[PlanStep] = []
        for step in raw_plan:
            errors = self._validate_step(step)
            if errors:
                logger.warning(
                    "planner.step_invalid",
                    step_id=step.get("step_id"),
                    tool=step.get("tool"),
                    errors=errors,
                )
                # 严重错误（工具不存在）→ 跳过；参数缺失 → 保留但记录
                if any("未注册" in e for e in errors):
                    continue
            validated.append(
                PlanStep(
                    step_id=step.get("step_id", len(validated) + 1),
                    action=step.get("action", ""),
                    tool=step.get("tool", ""),
                    tool_args=step.get("tool_args", {}),
                    rationale=step.get("rationale", ""),
                    parallel=bool(step.get("parallel", False)),
                )
            )

        return {
            "analysis_plan": validated,
            "current_step": 0,
            "status": "executing",
        }
