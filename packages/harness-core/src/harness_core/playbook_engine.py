"""Playbook DSL 引擎 — 从 YAML 配置驱动多步骤编排。

替代 orchestrator/app.py 的硬编码 if-else。
新增剧本只需在 config/playbooks.yaml 加条目 + 保证 Agent 有 AgentCard 即可。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)


def _deep_get(obj: dict, path: str, default: Any = None) -> Any:
    """字典点路径取值。如 'rca.root_cause' 取 obj['rca']['root_cause']"""
    parts = path.split(".", 1)
    val = obj.get(parts[0], {})
    if len(parts) > 1 and isinstance(val, dict):
        return _deep_get(val, parts[1], default)
    if isinstance(val, dict) and not parts:
        return val
    return val if val is not None else default


def _deep_set(obj: dict, path: str, value: Any) -> None:
    """字典点路径设值。如 'rca.root_cause' 设 obj['rca']['root_cause'] = value"""
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj.setdefault(p, {})
    obj[parts[-1]] = value


def _from_dotted(container: dict, dotted: str) -> Any:
    """从响应点路径取值，如 'response.evidence' → container['response']['evidence']"""
    prefix, _, rest = dotted.partition(".")
    if prefix == "response":
        return _deep_get(container, rest)
    if prefix == "result":
        return _deep_get(container, rest)
    return _deep_get(container, dotted)


def _eval_condition(expr: str | None, ctx: dict, req: dict) -> bool:
    """评估步骤条件表达式。

    支持语法:
      - "batch_id"                  → bool(ctx['batch_id'])
      - "not skip_triage"           → not bool(req.get('skip_triage'))
      - "not skip_triage and not defect_type"  → 组合
      - "rca.root_cause"            → bool(ctx['rca']['root_cause'])
    """
    if not expr:
        return True

    # 简单条件解析器（安全，不 eval）
    tokens = expr.strip().split()

    def _lookup(name: str) -> Any:
        if name == "not":
            return None  # 操作符
        if name == "and":
            return None
        if name == "or":
            return None
        # 查找 ctx
        if name in ctx:
            return ctx[name]
        # 查找 req
        if name in req:
            return req[name]
        # 点路径 ctx
        if "." in name:
            v = _deep_get(ctx, name)
            if v is not None:
                return v
        return False

    # 处理简单 "field_name" 形式
    if len(tokens) == 1 and tokens[0] not in ("not", "and", "or"):
        return bool(_lookup(tokens[0]))

    # 处理 "not field" 形式
    if len(tokens) == 2 and tokens[0] == "not":
        return not bool(_lookup(tokens[1]))

    # 处理 "not A and not B" 形式
    if len(tokens) >= 3:
        result = None
        current_op = None
        for t in tokens:
            if t == "not":
                current_op = "not"
            elif t == "and":
                current_op = "and"
            elif t == "or":
                current_op = "or"
            else:
                val = bool(_lookup(t))
                if current_op == "not":
                    val = not val
                    current_op = None
                if result is None:
                    result = val
                elif current_op == "and":
                    result = result and val
                    current_op = None
                elif current_op == "or":
                    result = result or val
                    current_op = None
                else:
                    result = val
        return bool(result)

    return True


@dataclass
class StepResult:
    """单步骤执行结果。"""
    step_name: str
    agent: str
    response: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    skipped: bool = False
    hitl_required: bool = False


@dataclass
class PlaybookDef:
    """Playbook YAML 定义的内存表示。"""
    name: str
    description: str
    steps: list[dict[str, Any]]


class PlaybookEngine:
    """从 YAML 加载 Playbook 并动态执行。

    用法:
        engine = PlaybookEngine("config/playbooks.yaml")
        result = await engine.execute(
            playbook="close_loop",
            ctx={"batch_id": "B001"},
            req={"message": "容量偏低"},
            trace_id="tr-xxx",
            session_id="sess-xxx",
            call_step=my_step_callback,
            emit_event=my_event_callback,
        )
    """

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.playbooks: dict[str, PlaybookDef] = {}
        self._loaded = False

    def load(self) -> None:
        """加载并解析 playbooks.yaml。"""
        if not self.config_path.exists():
            logger.warning("playbook.config_not_found", path=str(self.config_path))
            return

        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        playbooks_data = raw.get("playbooks", {})
        for name, pb in playbooks_data.items():
            self.playbooks[name] = PlaybookDef(
                name=name,
                description=pb.get("description", ""),
                steps=pb.get("steps", []),
            )
        self._loaded = True
        logger.info("playbook.loaded", count=len(self.playbooks))

    def list_playbooks(self) -> list[dict[str, Any]]:
        """列出可用 playbook（供 Planner Agent 用）。"""
        if not self._loaded:
            self.load()
        return [
            {"name": pb.name, "description": pb.description}
            for pb in self.playbooks.values()
        ]

    async def execute(
        self,
        playbook: str,
        ctx: dict[str, Any],
        req: dict[str, Any],
        trace_id: str,
        session_id: str,
        call_step: Any = None,
        emit_event: Any = None,
    ) -> dict[str, Any]:
        """执行 Playbook，返回最终结果。

        参数:
            playbook: playbook 名称
            ctx: PlatformContext dict
            req: 原始请求 dict
            trace_id: 全链路 trace ID
            session_id: 会话 ID
            call_step: async callable(agent_name, payload, step_def) → dict
            emit_event: async callable(event_type, step, agent, payload)
        """
        if not self._loaded:
            self.load()

        pb = self.playbooks.get(playbook)
        if not pb:
            raise ValueError(f"Unknown playbook: {playbook}")

        logger.info("playbook.execute", playbook=playbook, steps=len(pb.steps))

        result: dict[str, Any] = {
            "session_id": session_id,
            "trace_id": trace_id,
            "playbook": playbook,
            "status": "running",
            "current_step": "",
            "steps_completed": [],
            "step_results": {},
            "error": None,
        }

        for step_def in pb.steps:
            step_name = step_def.get("step", "unknown")
            step_type = step_def.get("type", "agent_call")
            agent = step_def.get("agent", "")
            required = step_def.get("required", False)

            # 1. 条件检查
            condition = step_def.get("condition")
            if condition:
                matched = _eval_condition(condition, ctx, req)
                if not matched:
                    logger.info("playbook.step_skipped", step=step_name, condition=condition)
                    s = StepResult(step_name=step_name, agent=agent, skipped=True)
                    result["steps_completed"].append(s.step_name)
                    result["step_results"][step_name] = {
                        "skipped": True,
                        "condition": condition,
                    }
                    continue

            result["current_step"] = step_name

            if emit_event:
                await emit_event("step_started", step_name, agent, f"开始执行: {step_name}")

            # 2. input_required 类型（确认/HITL）
            if step_type == "input_required":
                if emit_event:
                    await emit_event("input_required", step_name, agent,
                                     step_def.get("message", "需要人工确认"))
                result["status"] = "awaiting_confirm"
                result["current_step"] = step_name
                result["input_required"] = {
                    "step": step_name,
                    "message": step_def.get("message", ""),
                }
                return result

            # 3. agent_call 类型（默认）
            if step_type == "agent_call" or step_type is None:
                if not call_step:
                    raise ValueError(f"call_step required for agent step: {step_name}")

                try:
                    response = await call_step(agent, step_def, ctx)
                except Exception as exc:
                    logger.error("playbook.step_failed", step=step_name, agent=agent, error=str(exc))

                    # 检查 fallback 配置
                    fallback_cfg = step_def.get("fallback", {})
                    if fallback_cfg.get("on_failure"):
                        response = {
                            "_fallback": True,
                            "_fallback_reason": str(exc),
                        }
                    elif required:
                        result["status"] = "failed"
                        result["error"] = f"Step {step_name} failed: {exc}"
                        if emit_event:
                            await emit_event("failed", step_name, agent, str(exc))
                        return result
                    else:
                        s = StepResult(step_name=step_name, agent=agent, error=str(exc), skipped=True)
                        result["steps_completed"].append(s.step_name)
                        continue

                # 4. HITL 检查
                hitl_cfg = step_def.get("hitl_check")
                if hitl_cfg:
                    hitl_field = hitl_cfg.get("field", "")
                    if hitl_field:
                        requires = _from_dotted(response, hitl_field)
                        if bool(requires):
                            if emit_event:
                                await emit_event("hitl", step_name, agent,
                                                 f"{step_name} 需人工签核")
                            result["status"] = "hitl"
                            result["current_step"] = f"hitl_{step_name}"
                            result["hitl_request"] = {
                                "step": step_name,
                                "agent": agent,
                                "response": response,
                            }
                            return result

                # 5. 空值 fallback 检查
                fallback_cfg = step_def.get("fallback", {})
                on_empty = fallback_cfg.get("on_empty")
                if on_empty and response:
                    response_val = _from_dotted(response, on_empty)
                    if not response_val:
                        response = {
                            "_fallback": True,
                            "_fallback_reason": f"{on_empty} is empty",
                        }

                # 6. Context 写入
                context_write = step_def.get("context_write", {})
                if context_write:
                    for ctx_key, response_path in context_write.items():
                        val = _from_dotted(response, response_path)
                        if val is not None:
                            _deep_set(ctx, ctx_key, val)

                s = StepResult(step_name=step_name, agent=agent, response=response)
                result["steps_completed"].append(step_name)
                result["step_results"][step_name] = {
                    "agent": agent,
                    "status": "completed" if not response.get("_fallback") else "fallback",
                }
                if emit_event:
                    await emit_event("step_completed", step_name, agent)

        # 全部步骤完成
        result["status"] = "completed"
        result["current_step"] = "done"
        return result
