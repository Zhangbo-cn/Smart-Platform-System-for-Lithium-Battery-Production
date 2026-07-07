"""SmartRouter：PlaybookEngine 的 LLM 智能路由层。

用法：
  1. Orchestrator 启动时初始化 SmartRouter(agent_network, llm_base_url, llm_api_key)
  2. _call_step 中先调 router.suggest()，再 fallback 到 YAML
  3. router 返回 (agent_name, confidence, reasoning) + 内置缓存
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import structlog

try:
    from langsmith import traceable
except ImportError:
    traceable = lambda **kw: lambda f: f  # no-op fallback

from platform_contracts.agent_network import AgentNetwork

logger = structlog.get_logger(__name__)

_CACHE_TTL = 300

_ROUTER_SYSTEM_PROMPT = """你是一个锂电制造平台的智能路由专家。你的任务是根据当前上下文，从可用 Agent 中选择最适合执行下一步的 Agent。

可用 Agent 列表（按 capability 分组）：
{agent_catalog}

当前执行的剧本 (playbook)：{playbook}
已完成步骤：{completed_steps}
当前要执行的步骤：{current_step}

上下文关键信息：
- 缺陷类型：{defect_type}
- 严重度：{severity}
- 批次：{batch_id}
- 用户描述：{user_query}

规则：
1. 只能从"可用 Agent"中选择
2. 如果当前步骤的标准 Agent 完全合适，就返回它（不无故更换）
3. 只在以下情况建议更换：
   a. 标准 Agent 不处理当前缺陷类型
   b. 有更专业的 Agent 可直接处理
   c. 标准 Agent 不可达（但不要假设它已宕机）
4. 返回 JSON（不要 markdown 包裹）：
   {{"agent_name": "建议的agent名", "confidence": 0.0-1.0, "reasoning": "选择理由"}}
"""


class SmartRouter:
    """Playbook 智能路由 — 直接调 LLM API，不经 A2A。"""

    def __init__(
        self,
        agent_network: AgentNetwork,
        *,
        llm_base_url: str = "",
        llm_api_key: str = "",
        llm_model: str = "deepseek-chat",
        cache_ttl: int = _CACHE_TTL,
    ) -> None:
        self._network = agent_network
        self._llm_base_url = llm_base_url.rstrip("/")
        self._llm_api_key = llm_api_key
        self._llm_model = llm_model
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[str, float, float]] = {}
        self._enabled = bool(llm_base_url and llm_api_key)

    async def suggest(
        self,
        *,
        playbook: str,
        current_step: str,
        default_agent: str,
        completed_steps: list[str] = [],
        defect_type: str = "",
        severity: str = "medium",
        batch_id: str = "",
        user_query: str = "",
    ) -> tuple[str, float, str]:
        """LLM 建议当前步骤的最佳 Agent。

        返回 (agent_name, confidence, reasoning)。
        未配置 LLM / 调用失败 / 置信度不足 → 返回 (default_agent, 0.0, fallback)。
        """
        if not self._enabled:
            return default_agent, 0.0, "router_not_configured"

        cache_key = f"{playbook}:{current_step}:{defect_type}:{batch_id}"
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[2]) < self._cache_ttl:
            return cached[0], cached[1], "cached"

        prompt = self._build_prompt(
            playbook=playbook, current_step=current_step,
            completed_steps=completed_steps,
            defect_type=defect_type, severity=severity,
            batch_id=batch_id, user_query=user_query,
        )

        try:
            result = await self._call_llm(prompt)
            agent_name = result.get("agent_name", default_agent)
            confidence = float(result.get("confidence", 0.0))
            reasoning = str(result.get("reasoning", ""))

            if agent_name not in self._agent_names():
                logger.warning("smart_router.invalid_agent", suggested=agent_name, available=self._agent_names())
                agent_name = default_agent
                confidence = 0.0
                reasoning = f"'{agent_name}' not available"

            self._cache[cache_key] = (agent_name, confidence, time.time())
            logger.info("smart_router.suggest", playbook=playbook, step=current_step,
                        default=default_agent, suggested=agent_name, confidence=confidence)
            return agent_name, confidence, reasoning

        except Exception as exc:
            logger.warning("smart_router.llm_failed", error=str(exc))
            return default_agent, 0.0, f"llm_error: {exc}"

    # ── 内部 ──────────────────────────────────────────────────

    def _build_prompt(self, **kwargs) -> str:
        catalog = "\n".join(
            f"- {c.name}: {c.description}" + (f"\n  能力: {', '.join(c.capabilities)}" if c.capabilities else "")
            for c in self._network.list_all()
        )
        return _ROUTER_SYSTEM_PROMPT.format(
            agent_catalog=catalog,
            playbook=kwargs.get("playbook", ""),
            completed_steps=" → ".join(kwargs.get("completed_steps", [])),
            current_step=kwargs.get("current_step", ""),
            defect_type=kwargs.get("defect_type", ""),
            severity=kwargs.get("severity", "medium"),
            batch_id=kwargs.get("batch_id", ""),
            user_query=kwargs.get("user_query", ""),
        )

    def _agent_names(self) -> set[str]:
        return {c.name for c in self._network.list_all()}

    @traceable(run_type="llm", name="smart_router_llm")
    async def _call_llm(self, prompt: str) -> dict[str, Any]:
        """直接调 LLM chat completions API，不走 A2A。"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._llm_base_url}/chat/completions",
                json={
                    "model": self._llm_model,
                    "temperature": 0.0,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
                headers={"Authorization": f"Bearer {self._llm_api_key}", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        return json.loads(text)
