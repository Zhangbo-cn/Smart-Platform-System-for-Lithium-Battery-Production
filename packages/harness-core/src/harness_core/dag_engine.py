"""DAG 引擎：支持并行节点、条件分支、熔断、断点续跑的下一版 PlaybookEngine。

与旧版 PlaybookEngine 的区别：
  - 线性 for 循环 → 有向无环图（DAG）
  - 纯顺序执行 → 支持并行节点
  - 无条件 → 支持 if-else 分支
  - 简单 required → 支持熔断 + 回退

用法：
  engine = DAGEngine.from_yaml("config/playbooks.yaml")
  result = await engine.execute("investigate", ctx, req, call_step=..., emit_event=...)
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import structlog
import yaml

logger = structlog.get_logger(__name__)


# ── 数据模型 ──────────────────────────────────────────────


@dataclass
class DAGNode:
    """DAG 中的单个节点定义。"""
    id: str
    agent: str = ""
    type: str = "agent_call"  # agent_call | input_required | branch
    depends_on: list[str] = field(default_factory=list)  # 前置依赖节点 ID
    condition: str = ""       # 条件表达式（同 PlaybookEngine 语法）
    parallel: bool = False    # 同层是否可并行
    max_retry: int = 2        # 失败重试次数
    timeout: float = 120.0    # 单节点超时秒数
    fallback: dict | None = None  # {"on_failure": true, "on_empty": "field"}
    hitl_check: dict | None = None  # {"field": "response.requires_hitl", "on_hitl": "pause"}
    branches: list["DAGBranch"] = field(default_factory=list)  # 条件分支
    context_write: dict[str, str] = field(default_factory=dict)  # 结果写回 ctx 的映射


@dataclass
class DAGBranch:
    """条件分支：满足 condition 时走向 target_node。"""
    condition: str        # 如 "response.confidence >= 0.7"
    target: str           # 目标节点 ID


@dataclass
class DAGDef:
    name: str
    description: str = ""
    nodes: dict[str, DAGNode] = field(default_factory=dict)
    start_nodes: list[str] = field(default_factory=list)
    max_loop: int = 10

    def to_mermaid(self) -> str:
        """导出 Mermaid 流程图，用于调试/文档。"""
        lines = ["```mermaid", "graph TD"]
        node_ids: dict[str, str] = {}
        # 为每个节点分配短 ID
        for i, nid in enumerate(self.nodes, start=1):
            node_ids[nid] = f"N{i}"
        # 节点定义
        for nid, node in self.nodes.items():
            label_parts = [nid]
            if node.agent:
                label_parts.append(f"agent:{node.agent}")
            if node.parallel:
                label_parts.append("parallel")
            if node.type == "input_required":
                label_parts.append("HITL")
            label = " ".join(label_parts)
            lines.append(f"    {node_ids[nid]}[{label}]")
        # 依赖关系
        for nid, node in self.nodes.items():
            if node.depends_on:
                for dep in node.depends_on:
                    if dep in node_ids:
                        lines.append(f"    {node_ids[dep]} --> {node_ids[nid]}")
        lines.append("```")
        return "\n".join(lines)


@dataclass
class DAGState:
    """DAG 执行状态（可序列化，用于断点续跑）。"""
    session_id: str
    playbook: str
    status: str = "running"    # running | completed | failed | hitl
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    skipped_nodes: list[str] = field(default_factory=list)
    node_results: dict[str, dict] = field(default_factory=dict)
    current_step: str = ""
    error: str | None = None
    hitl_request: dict | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


# ── 引擎核心 ──────────────────────────────────────────────


class DAGEngine:
    """DAG 执行引擎。"""

    def __init__(self) -> None:
        self.playbooks: dict[str, DAGDef] = {}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DAGEngine":
        """从 YAML 文件加载 DAG 定义。"""
        engine = cls()
        path = Path(path)
        if not path.exists():
            logger.warning("dag.config_not_found", path=str(path))
            return engine

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        playbooks_data = raw.get("playbooks", {})
        for name, pb in playbooks_data.items():
            engine.playbooks[name] = _parse_dag_def(name, pb)
        logger.info("dag.loaded", count=len(engine.playbooks))
        return engine

    async def execute(
        self,
        playbook: str,
        ctx: dict[str, Any],
        req: dict[str, Any],
        trace_id: str,
        session_id: str,
        call_step: Callable | None = None,
        emit_event: Callable | None = None,
        state: DAGState | None = None,  # 断点续跑
    ) -> dict[str, Any]:
        """执行 DAG Playbook。"""
        dag = self.playbooks.get(playbook)
        if not dag:
            raise ValueError(f"Unknown playbook: {playbook}")

        state = state or DAGState(
            session_id=session_id,
            playbook=playbook,
            created_at=time.time(),
        )
        state.updated_at = time.time()

        # BFS 拓扑执行
        ready = list(dag.start_nodes)
        visited = set(state.completed_nodes) | set(state.failed_nodes) | set(state.skipped_nodes)
        loop_count = 0

        while ready and loop_count < dag.max_loop:
            loop_count += 1

            # 过滤已完成的
            ready = [nid for nid in ready if nid not in visited]

            if not ready:
                break

            # 按分支切分：可并行的走 gather，不可并行的走顺序
            parallel_group, sequential = self._partition_nodes(ready, dag)

            # 执行并行组
            if parallel_group:
                tasks = [
                    self._run_node(nid, dag, ctx, req, trace_id, session_id,
                                   call_step, emit_event, state)
                    for nid in parallel_group
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for nid, res in zip(parallel_group, results):
                    if isinstance(res, Exception):
                        logger.error("dag.node_failed", node=nid, error=str(res))
                        state.failed_nodes.append(nid)
                    visited.add(nid)

            # 执行顺序组
            for nid in sequential:
                try:
                    await self._run_node(nid, dag, ctx, req, trace_id, session_id,
                                         call_step, emit_event, state)
                except Exception as exc:
                    logger.error("dag.node_failed", node=nid, error=str(exc))
                    state.failed_nodes.append(nid)
                visited.add(nid)

            # 检查中断条件（HITL 或等待确认）
            if state.status in ("hitl", "awaiting_confirm"):
                break

            # 计算下一批就绪节点
            ready = self._next_ready(dag, state, ctx, req)

        # 最终状态（保留 hitl/awaiting_confirm/failed 不变）
        if state.status not in ("hitl", "awaiting_confirm", "failed"):
            state.status = "completed"
            state.current_step = "done"

        return self._build_result(dag, state, ctx)

    # ── 内部 ──────────────────────────────────────────────

    async def _run_node(
        self,
        nid: str,
        dag: DAGDef,
        ctx: dict[str, Any],
        req: dict[str, Any],
        trace_id: str,
        session_id: str,
        call_step: Callable | None,
        emit_event: Callable | None,
        state: DAGState,
    ) -> dict:
        node = dag.nodes[nid]
        state.current_step = nid

        if emit_event:
            await emit_event("step_started", nid, node.agent, f"执行: {nid}")

        # 条件检查
        if node.condition and not self._eval_condition(node.condition, ctx, req):
            state.skipped_nodes.append(nid)
            if emit_event:
                await emit_event("step_completed", nid, node.agent)
            return {"skipped": True, "condition": node.condition}

        # input_required 类型
        if node.type == "input_required":
            state.status = "awaiting_confirm"
            state.hitl_request = {"step": nid, "message": "需要人工确认"}
            if emit_event:
                await emit_event("input_required", nid, node.agent, "需要人工确认")
            return {"status": "input_required"}

        # agent_call 类型
        if node.type == "agent_call" and call_step:
            last_err: Exception | None = None
            for attempt in range(1, node.max_retry + 2):
                try:
                    response = await asyncio.wait_for(
                        call_step(node.agent, {"step": nid}, ctx),
                        timeout=node.timeout,
                    )
                except Exception as exc:
                    last_err = exc
                    logger.warning("dag.retry", node=nid, attempt=attempt, error=str(exc))
                    if attempt < node.max_retry + 1:
                        await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                else:
                    break
            else:
                # 全部重试失败 → 检查 fallback
                if node.fallback and node.fallback.get("on_failure"):
                    response = {"_fallback": True, "_fallback_reason": str(last_err)}
                else:
                    state.failed_nodes.append(nid)
                    state.error = f"Node {nid} failed after {node.max_retry + 1} retries: {last_err}"
                    state.status = "failed"
                    raise last_err  # type: ignore[misc]

            # HITL 检查
            if node.hitl_check:
                hitl_field = node.hitl_check.get("field", "")
                if hitl_field:
                    val = self._deep_get(response, hitl_field.replace("response.", "", 1))
                    if bool(val):
                        state.status = "hitl"
                        state.hitl_request = {"step": nid, "agent": node.agent, "response": response}
                        if emit_event:
                            await emit_event("hitl", nid, node.agent, f"{nid} 需人工签核")
                        return response

            # Context 写入
            if node.context_write:
                for ctx_key, resp_path in node.context_write.items():
                    val = self._deep_get(response, resp_path.replace("response.", "", 1))
                    if val is not None:
                        self._deep_set(ctx, ctx_key, val)

            state.completed_nodes.append(nid)
            state.node_results[nid] = {"status": "completed", "agent": node.agent}

            if emit_event:
                await emit_event("step_completed", nid, node.agent)

            return response

        return {}

    def _next_ready(self, dag: DAGDef, state: DAGState, ctx: dict, req: dict) -> list[str]:
        """计算下一批依赖已满足的节点。"""
        finished = set(state.completed_nodes) | set(state.failed_nodes) | set(state.skipped_nodes)
        ready = []
        for nid, node in dag.nodes.items():
            if nid in finished:
                continue
            if node.condition and not self._eval_condition(node.condition, ctx, req):
                continue
            if all(dep in finished for dep in node.depends_on):
                ready.append(nid)
        return ready

    def _partition_nodes(
        self, node_ids: list[str], dag: DAGDef
    ) -> tuple[list[str], list[str]]:
        """将就绪节点分为并行组和顺序组。"""
        parallel = []
        sequential = []
        for nid in node_ids:
            node = dag.nodes.get(nid)
            if node and node.parallel:
                parallel.append(nid)
            else:
                sequential.append(nid)
        return parallel, sequential

    @staticmethod
    def _eval_condition(expr: str, ctx: dict, req: dict) -> bool:
        """评估条件表达式（完整版，支持复合表达式）。

        支持语法:
          - "batch_id"                  → bool(ctx['batch_id'])
          - "not skip_triage"           → not bool(req.get('skip_triage'))
          - "not skip_triage and not defect_type"  → 组合
          - "rca.root_cause"            → bool(ctx['rca']['root_cause'])
        """
        if not expr:
            return True
        tokens = expr.strip().split()

        def _lookup(name: str) -> Any:
            if name in ("not", "and", "or"):
                return None
            if name in ctx:
                return ctx[name]
            if name in req:
                return req[name]
            if "." in name:
                v = DAGEngine._deep_get(ctx, name)
                if v is not None:
                    return v
            return False

        # 简单 "field" 形式
        if len(tokens) == 1 and tokens[0] not in ("not", "and", "or"):
            return bool(_lookup(tokens[0]))

        # "not field" 形式
        if len(tokens) == 2 and tokens[0] == "not":
            return not bool(_lookup(tokens[1]))

        # "not A and not B" / "A and B" 等复合形式
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

    @staticmethod
    def _deep_get(obj: dict, path: str, default: Any = None) -> Any:
        parts = path.split(".")
        for p in parts:
            if isinstance(obj, dict):
                obj = obj.get(p, {})
            else:
                return default
        return obj if obj != {} else default

    @staticmethod
    def _deep_set(obj: dict, path: str, value: Any) -> None:
        parts = path.split(".")
        for p in parts[:-1]:
            obj = obj.setdefault(p, {})
        obj[parts[-1]] = value

    @staticmethod
    def _build_result(dag: DAGDef, state: DAGState, ctx: dict) -> dict:
        return {
            "playbook": state.playbook,
            "status": state.status,
            "current_step": state.current_step,
            "error": state.error,
            "completed": state.completed_nodes,
            "failed": state.failed_nodes,
            "skipped": state.skipped_nodes,
            "hitl_request": state.hitl_request,
            "node_count": len(dag.nodes),
        }


# ── 解析 YAML ──────────────────────────────────────────────


def _parse_dag_def(name: str, raw: dict) -> DAGDef:
    """从 YAML dict 解析为 DAGDef。"""
    dag = DAGDef(name=name, description=raw.get("description", ""))
    nodes_raw = raw.get("nodes", raw.get("steps", []))
    start_nodes: list[str] = []

    if isinstance(nodes_raw, dict):
        # 新格式：map 形式
        for nid, node_raw in nodes_raw.items():
            dag.nodes[nid] = _parse_node(nid, node_raw)
            node = dag.nodes[nid]
            if not node.depends_on:
                start_nodes.append(nid)
    elif isinstance(nodes_raw, list):
        # 兼容旧格式：列表 → 串行 DAG
        prev = None
        for i, step_raw in enumerate(nodes_raw):
            nid = step_raw.get("step", f"step_{i}")
            dag.nodes[nid] = _parse_node(nid, step_raw)
            node = dag.nodes[nid]
            if prev and not node.depends_on:
                node.depends_on = [prev]
            if not node.depends_on:
                start_nodes.append(nid)
            prev = nid

    dag.start_nodes = start_nodes or [list(dag.nodes.keys())[0]]
    return dag


def _parse_node(nid: str, raw: dict) -> DAGNode:
    branches = []
    for br in raw.get("branches", []):
        branches.append(DAGBranch(
            condition=br.get("condition", ""),
            target=br.get("target", ""),
        ))

    return DAGNode(
        id=nid,
        agent=raw.get("agent", ""),
        type=raw.get("type", "agent_call"),
        depends_on=raw.get("depends_on", raw.get("depend_nodes", [])),
        condition=raw.get("condition", ""),
        parallel=raw.get("parallel", False),
        max_retry=raw.get("max_retry", raw.get("retry", 2)),
        timeout=raw.get("timeout", 120.0),
        fallback=raw.get("fallback"),
        hitl_check=raw.get("hitl_check"),
        branches=branches,
        context_write=raw.get("context_write", {}),
    )
