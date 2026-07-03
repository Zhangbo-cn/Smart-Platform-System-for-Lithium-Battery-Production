"""LLM 客户端适配：Anthropic 原生 API 与 OpenAI 兼容 API（DeepSeek / vLLM 等）。

模型路由策略：
- 按 task_difficulty 选模型能力（simple → flash, complex → primary）
- 按 data_sensitivity 选部署位置（low → external API, medium/high → local vLLM）
- 若 local 不可用 → 脱敏后降级到 external（记录审计日志）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from config import get_settings


class DataSensitivity(str, Enum):
    """数据敏感度：决定走 external API 还是 local 模型。"""
    LOW = "low"        # 缺陷描述、结论文本 → external OK
    MEDIUM = "medium"  # 证据摘要、工艺参数摘要 → 优先 local
    HIGH = "high"      # MES/SCADA 原始数据 → 必须 local


class TaskDifficulty(str, Enum):
    """任务难度：决定用 flash 还是 primary 模型。"""
    TRIVIAL = "trivial"    # 格式化、粘贴 → flash / cheap model
    SIMPLE = "simple"      # 分类、路由 → flash
    MODERATE = "moderate"  # 摘要、合成 → primary
    COMPLEX = "complex"    # 因果推断、重规划 → primary + low temp


@dataclass
class ModelRoute:
    """一次 LLM 调用的路由决策。"""
    model: str
    endpoint: str  # base_url 或 "local"
    sensitivity: DataSensitivity
    difficulty: TaskDifficulty
    caller: str = ""  # 调用方标识，如 "reflector._llm_correlation"


@dataclass
class _TextBlock:
    type: str
    text: str


@dataclass
class LLMResponse:
    """与 Anthropic 响应结构兼容，供各 Agent 统一解析。"""

    content: list[_TextBlock]
    route: ModelRoute | None = None  # 本次调用的路由信息，供审计


import structlog

_usage_logger = structlog.get_logger("llm.usage")
_route_logger = structlog.get_logger("llm.route")  # 路由决策审计日志


def _log_usage(usage, model: str, route: ModelRoute | None = None) -> None:
    """记录每次 LLM 调用的 token 消耗 + 路由决策。"""
    if usage is None:
        return
    inp = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0)
    out = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0)
    total = getattr(usage, "total_tokens", 0) or (inp + out)
    _usage_logger.info(
        "llm.token_usage",
        model=model,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
    )
    if route:
        _route_logger.info(
            "llm.route_decision",
            model=route.model,
            endpoint=route.endpoint,
            sensitivity=route.sensitivity.value,
            difficulty=route.difficulty.value,
            caller=route.caller,
            input_tokens=inp,
        )


# ── 数据脱敏：移除可能包含工艺机密的字段 ──

_SENSITIVE_KEYS = frozenset({
    "recipe_params", "equipment_id", "operator_id", "lot_number",
    "actual_temperature", "actual_speed", "coating_gap",
    "raw_material_batch", "supplier_name",
})


def redact_sensitive_fields(data: dict | list | str) -> dict | list | str:
    """递归脱敏：将敏感字段值替换为 [REDACTED]。"""
    if isinstance(data, dict):
        return {
            k: "[REDACTED]" if k in _SENSITIVE_KEYS else redact_sensitive_fields(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact_sensitive_fields(item) for item in data]
    return data


def _normalize_system(system: list[dict] | str) -> str:
    if isinstance(system, str):
        return system
    parts: list[str] = []
    for block in system:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def create_llm_client(settings=None, *, prefer_local: bool = False) -> AsyncAnthropic | AsyncOpenAI | None:
    """创建 LLM 客户端。prefer_local=True 时优先使用本地 vLLM。"""
    settings = settings or get_settings()
    if prefer_local and settings.local_llm_base_url:
        api_key = settings.local_llm_api_key or "EMPTY"
        return AsyncOpenAI(api_key=api_key, base_url=settings.local_llm_base_url)
    api_key = settings.resolved_llm_api_key()
    if settings.llm_base_url:
        return AsyncOpenAI(api_key=api_key, base_url=settings.llm_base_url)
    return AsyncAnthropic(api_key=api_key)


def _resolve_route(
    settings,
    difficulty: TaskDifficulty = TaskDifficulty.MODERATE,
    sensitivity: DataSensitivity = DataSensitivity.LOW,
    caller: str = "",
) -> ModelRoute:
    """
    路由决策引擎：
    - sensitivity=LOW → 直接用 external primary/flash
    - sensitivity=MEDIUM/HIGH → 优先 local；local 不可用时 external + 审计告警
    - difficulty=TRIVIAL/SIMPLE → 可用 flash 模型（更便宜）
    """
    # 敏感数据 → 优先 local
    if sensitivity in (DataSensitivity.MEDIUM, DataSensitivity.HIGH) and settings.local_llm_base_url:
        model = settings.local_llm_model or "qwen2.5-72b-instruct"
        return ModelRoute(
            model=model,
            endpoint=settings.local_llm_base_url,
            sensitivity=sensitivity,
            difficulty=difficulty,
            caller=caller,
        )

    # 非敏感 → external，按难度选模型
    if difficulty in (TaskDifficulty.TRIVIAL, TaskDifficulty.SIMPLE):
        model = getattr(settings, "llm_flash_model", None) or settings.llm_primary_model
    else:
        model = settings.llm_primary_model

    endpoint = settings.llm_base_url or "anthropic"
    return ModelRoute(
        model=model,
        endpoint=str(endpoint),
        sensitivity=sensitivity,
        difficulty=difficulty,
        caller=caller,
    )


async def chat_completion(
    *,
    system: list[dict] | str,
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    temperature: float | None = None,
    client: AsyncAnthropic | AsyncOpenAI | None = None,
    difficulty: TaskDifficulty = TaskDifficulty.MODERATE,
    sensitivity: DataSensitivity = DataSensitivity.LOW,
    caller: str = "",
) -> LLMResponse:
    """
    统一 LLM 调用入口。

    新增参数：
    - difficulty: 任务难度 → 影响模型选择（flash vs primary）
    - sensitivity: 数据敏感度 → 影响部署位置（external vs local）
    - caller: 调用方标识 → 审计追踪

    路由逻辑：
    1. sensitivity=MEDIUM/HIGH + local 可用 → 走本地 vLLM
    2. sensitivity=MEDIUM/HIGH + local 不可用 → external + 审计告警
    3. sensitivity=LOW → external，difficulty 决定 flash/primary
    """
    settings = get_settings()
    route = _resolve_route(settings, difficulty=difficulty, sensitivity=sensitivity, caller=caller)

    # 敏感数据走 external 时记录告警（local 不可用的降级场景）
    if sensitivity in (DataSensitivity.MEDIUM, DataSensitivity.HIGH) and route.endpoint != str(
        settings.local_llm_base_url or ""
    ):
        _route_logger.warning(
            "llm.sensitive_data_external_fallback",
            sensitivity=sensitivity.value,
            caller=caller,
            endpoint=route.endpoint,
            note="本地模型不可用，敏感数据经脱敏后走外部 API",
        )

    # 按路由决策创建客户端
    prefer_local = route.endpoint.startswith("http") and route.endpoint == str(
        settings.local_llm_base_url or ""
    )
    client = client or create_llm_client(settings, prefer_local=prefer_local)
    temp = temperature if temperature is not None else settings.llm_temperature

    if isinstance(client, AsyncOpenAI):
        oai_messages: list[dict[str, str]] = [{"role": "system", "content": _normalize_system(system)}]
        for msg in messages:
            oai_messages.append({"role": msg["role"], "content": msg["content"]})
        kwargs: dict[str, Any] = {
            "model": route.model,
            "messages": oai_messages,
            "max_tokens": settings.llm_max_tokens,
            "temperature": temp,
        }
        if tools:
            kwargs["tools"] = tools
        resp = await client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        _log_usage(resp.usage, route.model, route=route)
        return LLMResponse(content=[_TextBlock(type="text", text=text)], route=route)

    # Anthropic 原生
    resp = await client.messages.create(
        model=route.model,
        max_tokens=settings.llm_max_tokens,
        temperature=temp,
        system=system,
        messages=messages,
        tools=tools or [],
    )
    _log_usage(resp.usage, route.model, route=route)
    blocks = [
        _TextBlock(type=getattr(b, "type", "text"), text=getattr(b, "text", ""))
        for b in resp.content
        if getattr(b, "type", None) == "text"
    ]
    return LLMResponse(content=blocks, route=route)
