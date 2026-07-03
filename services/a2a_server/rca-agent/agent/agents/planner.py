from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.agents.base import BaseAgent
from agent.llm import DataSensitivity, TaskDifficulty
from agent.state import PlanStep, QualityAnalysisState

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "planner_system.md"


class PlannerAgent(BaseAgent):
    name = "planner"

    def _system_prompt(self) -> list[dict]:
        text = PROMPT_PATH.read_text(encoding="utf-8")
        return [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}},
        ]

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
        plan: list[PlanStep] = []
        try:
            plan = json.loads(text)["steps"]
        except (json.JSONDecodeError, KeyError):
            plan = []

        return {
            "analysis_plan": plan,
            "current_step": 0,
            "status": "executing",
        }
