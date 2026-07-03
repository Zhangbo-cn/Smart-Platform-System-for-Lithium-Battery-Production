from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from agent.agents.base import BaseAgent  # 继承父类（共享配置/LLM，但 Executor 基本不调 LLM）
from agent.state import QualityAnalysisState, ToolCallRecord  # 图状态类型，单次工具调用的记录结构
from agent.tools.registry import ToolRegistry  # 工具注册表，真正发起 MCP 调用
from harness_core.context.compressor import ContextCompressor


class ExecutorAgent(BaseAgent):
    name = "executor"

    def __init__(
        self,
        registry: ToolRegistry,
        llm_client=None,
        compressor: ContextCompressor | None = None,
    ) -> None:
        """
        比父类多注入一个 ToolRegistry（API 启动时 bootstrap_registry 填好各 MCP 工具）
        Executor 不靠 LLM 决策，只负责按 plan 调 registry.invoke
        """
        super().__init__(llm_client=llm_client)
        self.registry = registry
        self.compressor = compressor or ContextCompressor()

    @staticmethod
    def _args_key(args: dict[str, Any]) -> str:
        return json.dumps(args or {}, sort_keys=True, default=str)

    def _find_existing_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        records: list[ToolCallRecord],
    ) -> ToolCallRecord | None:
        key = self._args_key(args)
        for record in records:
            if record.get("tool") == tool_name and self._args_key(record.get("args") or {}) == key:
                if record.get("error") is None:
                    return record
        return None

    async def _execute_step(  # 执行单步工具
            self,
            step: dict[str, Any],  # 输入 step（来自 plan）典型字段
            state: QualityAnalysisState,
    ) -> ToolCallRecord:
        """执行单个步骤的工具调用"""
        tool_name = step.get("tool")
        if not tool_name:  # 没有tool，返回错误记录"No tool specified"
            return ToolCallRecord(
                step_id=step["step_id"],
                tool="",
                args={},
                result=None,
                duration_ms=0,
                error="No tool specified",
            )

        tool_args = step.get("tool_args", {})
        existing = self._find_existing_call(
            tool_name,
            tool_args,
            list(state.get("tool_calls") or []),
        )
        if existing is not None:
            return ToolCallRecord(
                step_id=step["step_id"],
                tool=tool_name,
                args=tool_args,
                result=existing.get("result"),
                duration_ms=0,
                error=None,
            )

        """
        有tool，await self.registry.invoke(...)
            内部做：权限检查->审计日志->调MCP handler
            成功，填返回值reslut；异常：捕获进error字段
        """
        start = time.perf_counter()
        error = None
        result = None
        try:
            result = await self.registry.invoke(
                tool_name,
                tool_args,
                user_id=state.get("user_id", ""),
                user_role=state.get("user_role", ""),
            )
        except Exception as exc:
            error = str(exc)

        if result is not None and error is None:
            result = self.compressor.compress(result)

        return ToolCallRecord(
            step_id=step["step_id"],
            tool=tool_name,
            args=tool_args,
            result=result,  # MCP返回的数据
            duration_ms=int((time.perf_counter() - start) * 1000),
            error=error,
        )

    """
    图节点入口(核心逻辑)，LangGraph 每次进入 executor 节点都会调这个方法.
    """
    async def run(self, state: QualityAnalysisState) -> dict[str, Any]:
        plan = state.get("analysis_plan") or []  # 首轮执行：Planner 生成的计划，从游标往后执行
        records: list[ToolCallRecord] = list(state.get("tool_calls") or [])

        # 补查轮次：Reflector 通过 additional_queries 下发新的取证步骤
        # 这些步骤优先执行，且不依赖 current_step 游标（它们是增量补查，不在原 plan 里）
        additional = state.get("additional_queries") or []
        if additional:  # 补查优先(reflection返回)
            remaining_steps = self._normalize_steps(additional, base_id=len(plan) + len(records))
        else:
            # 首轮：执行原始计划里尚未执行的步骤
            remaining_steps = plan[state.get("current_step", 0):]

        if not remaining_steps:
            return {
                "tool_calls": records,
                "current_step": len(plan),
                "status": "reflecting",
            }

        # 识别可并行执行的步骤（通过 parallel 标记）
        parallel_steps = []
        sequential_steps = []

        for step in remaining_steps:
            if step.get("parallel", False):
                parallel_steps.append(step)  # 可并行
            else:
                sequential_steps.append(step)  # 必须顺序

        # 策略：如果有多个可并行步骤，则并行执行；否则顺序执行
        if len(parallel_steps) > 1:  # 并行 + 再顺序
            # 并行执行模式（Orchestrator-Worker）
            tasks = [
                self._execute_step(step, state)
                for step in parallel_steps
            ]
            parallel_results = await asyncio.gather(*tasks, return_exceptions=True)  # 并行跑完所有 parallel=true 的步骤

            # 处理并行结果
            for result in parallel_results:
                if isinstance(result, Exception):
                    # 异常转为错误记录
                    records.append(
                        ToolCallRecord(
                            step_id=-1,
                            tool="unknown",
                            args={},
                            result=None,
                            duration_ms=0,
                            error=str(result),
                        )
                    )
                else:
                    if not self._find_existing_call(
                        result["tool"], result.get("args") or {}, records
                    ):
                        records.append(result)

            # 顺序执行剩余步骤
            # 有先后依赖必须一个一个跑（如先查批次，再查明细）
            for step in sequential_steps:
                record = await self._execute_step(step, state)
                if not self._find_existing_call(
                    record["tool"], record.get("args") or {}, records
                ):
                    records.append(record)
        else:
            # 全部顺序执行（兼容原有逻辑）
            for step in remaining_steps:
                record = await self._execute_step(step, state)
                if not self._find_existing_call(
                    record["tool"], record.get("args") or {}, records
                ):
                    records.append(record)

        return {
            "tool_calls": records,
            "current_step": len(plan),
            # 补查步骤已消费，清空以免下轮重复执行
            "additional_queries": [],
            "status": "reflecting",
        }

    @staticmethod
    def _normalize_steps(steps: list[dict[str, Any]], base_id: int) -> list[dict[str, Any]]:
        """给补查步骤补齐 step_id，缺省 parallel=True（补查通常是独立取证）。"""
        normalized = []
        for i, step in enumerate(steps, start=1):
            s = dict(step)
            s.setdefault("step_id", base_id + i)
            s.setdefault("parallel", True)
            normalized.append(s)
        return normalized
