"""
统一 run 接口、共享 LLM/配置、把模型调用收口到 call_llm，让四个节点只管各自业务逻辑。
"""

from __future__ import annotations  # 推迟类型注解求值，允许写 dict[str, Any]、QualityAnalysisState 等前向引用，不必加引号

from abc import ABC, abstractmethod  # 抽象基类：ABC 定义抽象类，abstractmethod 标记子类必须实现的 run()
from typing import Any  # 类型标注：表示「任意类型」，如 dict[str, Any]

import structlog  # 结构化日志（JSON/键值对，便于检索）

from agent.llm import (
    DataSensitivity,
    LLMResponse,
    TaskDifficulty,
    chat_completion,
    create_llm_client,
)
from agent.state import QualityAnalysisState  # LangGraph 图里流转的状态类型（用户问题、计划、证据等）
from config import get_settings  # 读取 .env / 环境变量，返回配置单例
# 单例（Singleton） = 整个程序里只创建一份配置对象，到处调用 get_settings() 拿到的都是同一个实例。
logger = structlog.get_logger(__name__)


class BaseAgent(ABC):
    """
    ABC：抽象类，不能直接 BaseAgent() 实例化
    name：Agent 标识；子类覆盖，如 PlannerAgent.name = "planner"，便于日志/追踪
    """
    name: str = "base"

    def __init__(self, llm_client=None) -> None:
        self.settings = get_settings()  # config.get_settings() -> 模型名、API Key、温度等
        self.llm = llm_client or create_llm_client(self.settings)  # Anthropic 或 OpenAI 兼容客户端（DeepSeek/vLLM）

    @abstractmethod
    async def run(self, state: QualityAnalysisState) -> dict[str, Any]:
        """
        抽象方法（子类必须实现）
        输入：QualityAnalysisState（用户问题、计划、工具结果、根因等，在 state.py 定义）
        输出：dict，写回图的 state（如 analysis_plan、tool_calls、root_cause）
        LangGraph 节点本质：state 更新 = await agent.run(state)

        Agent	    run 做什么
        Planner	    调 LLM 生成分析计划
        Executor	按计划调 MCP 工具（基本不调 LLM）
        Reflector	FMEA 规则 + 必要时调 LLM 判断根因
        Reporter	调 LLM 把结论写成报告
        """
        ...

    async def call_llm(
        self,
        system: list[dict] | str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        difficulty: TaskDifficulty = TaskDifficulty.MODERATE,
        sensitivity: DataSensitivity = DataSensitivity.LOW,
        caller: str = "",
    ) -> LLMResponse:
        """
        统一的 LLM 调用入口，支持模型路由。

        difficulty:
            TRIVIAL  — 纯格式化、粘贴（d4_writer, evidence_appendix）
            SIMPLE   — 分类、路由决策（Planner 路由）
            MODERATE — 摘要、计划生成、报告合成（Planner/Reporter 节点）
            COMPLEX  — 因果推断、证据关联、重规划（Reflector）

        sensitivity:
            LOW     — 缺陷描述、结论文本 → external API 安全
            MEDIUM  — 证据摘要、参数趋势描述 → 优先 local
            HIGH    — MES/SCADA 原始数据 → 必须 local（不可用时拒绝调用）
        """
        return await chat_completion(
            system=system,
            messages=messages,
            tools=tools,
            temperature=temperature,
            client=self.llm,
            difficulty=difficulty,
            sensitivity=sensitivity,
            caller=caller or f"{self.name}",
        )
