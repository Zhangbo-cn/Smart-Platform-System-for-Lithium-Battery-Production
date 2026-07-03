"""Planner ReAct：LLM + 规划 Tool Use 循环（不调用业务 MCP / 不 delegate 业务服务）。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from platform_contracts.plan_result import PlanParams, PlanResult, PlannerRequest
from plan_engine import plan as rule_plan
from planning_tools import build_tool_schemas, execute_planning_tool
from settings import PlannerSettings

logger = structlog.get_logger(__name__)


def _build_system_prompt() -> str:
    """动态构建 system prompt，playbook 列表随注册的 Agent 变化。"""
    schemas = build_tool_schemas()
    submit_enum = []
    for s in schemas:
        props = s.get("function", {}).get("parameters", {}).get("properties", {})
        playbook_prop = props.get("playbook", {})
        submit_enum = playbook_prop.get("enum", ["investigate", "trace_only", "rca", "close_loop"])
        break
    playbooks_str = "、".join(submit_enum)
    return f"""你是锂电质量平台的 Planner Agent。职责：把用户自然语言转成 playbook + 参数，交给 Orchestrator 执行。

约束（必须遵守）：
1. 只能输出已实现剧本：{playbooks_str}。
2. 可调用 list_playbooks、get_capability_card 了解能力与剧本；禁止调用 trace/rca/8d 等业务服务。
3. 最终必须调用 submit_plan 提交结果。
4. investigate/trace_only/close_loop 通常需要 batch_id；从用户文本提取批次号（如 B202406001）。
5. close_loop 表示 RCA 已完成后的 8D 闭环；用户说「出8D」「CAPA」时优先考虑。
6. trace_only 仅查批次流转；用户说「查批次」「追溯」且无深度分析诉求时用。
7. 单点根因、无批次上下文时用 rca；有批次且要全流程用 investigate。

思考后选工具，一轮只做一个 tool call；信息足够时直接 submit_plan。"""


def _llm_configured(settings: PlannerSettings) -> bool:
    return bool(settings.llm_base_url.strip() and settings.llm_api_key.strip())


async def _chat_completion(
    settings: PlannerSettings,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "messages": messages,
        "tools": build_tool_schemas(),
        "tool_choice": "auto",
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _plan_from_submit(plan_dict: dict[str, Any]) -> PlanResult:
    params = plan_dict.get("params") or {}
    return PlanResult(
        playbook=plan_dict["playbook"],
        params=PlanParams(**{k: v for k, v in params.items() if v is not None or k in params}),
        confidence=float(plan_dict.get("confidence", 0.8)),
        reasoning=str(plan_dict.get("reasoning", "")),
    )


async def plan_with_react(req: PlannerRequest, settings: PlannerSettings) -> PlanResult:
    """ReAct 规划；LLM 不可用或失败时回退规则引擎。"""
    if settings.planner_mode == "rule" or not _llm_configured(settings):
        if settings.planner_mode == "react" and not _llm_configured(settings):
            logger.warning("planner.react_fallback", reason="llm_not_configured")
        return rule_plan(req)

    user_lines = [f"用户消息: {req.message}"]
    if req.playbook:
        user_lines.append(f"调用方倾向 playbook: {req.playbook}")
    if req.batch_id:
        user_lines.append(f"已知 batch_id: {req.batch_id}")
    if req.factory_id:
        user_lines.append(f"factory_id: {req.factory_id}")
    if req.defect_type:
        user_lines.append(f"defect_type: {req.defect_type}")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": "\n".join(user_lines)},
    ]

    try:
        for turn in range(settings.max_react_turns):
            data = await _chat_completion(settings, messages)
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                logger.warning("planner.react_no_tool_call", turn=turn)
                break

            messages.append(message)
            submitted: PlanResult | None = None

            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}

                logger.info("planner.tool_call", tool=name, turn=turn)
                content, plan_dict = execute_planning_tool(name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", f"call_{turn}"),
                        "content": content,
                    }
                )
                if plan_dict is not None:
                    submitted = _plan_from_submit(plan_dict)

            if submitted is not None:
                logger.info(
                    "planner.react_done",
                    playbook=submitted.playbook,
                    confidence=submitted.confidence,
                    turns=turn + 1,
                )
                return submitted

        logger.warning("planner.react_exhausted", max_turns=settings.max_react_turns)
    except Exception as exc:  # noqa: BLE001
        logger.exception("planner.react_failed", error=str(exc))

    return rule_plan(req)
