"""AsyncA2AServer：A2A Agent 基类，内置 LangGraph 适配（thread_id ↔ session_id + HITL resume）。

用法：
  # 方式 1：LangGraph Agent（推荐）—— 设置 graph 属性，自动处理生命周期
  class MyServer(AsyncA2AServer):
      @property
      def graph(self): return my_compiled_graph

      async def build_input(self, payload, session_id):
          return {"messages": [HumanMessage(content=payload["query"])]}

      async def extract_output(self, state):
          return {"answer": state["messages"][-1].content}

  # 方式 2：手动控制 —— 直接 override handle_task（向后兼容）
  class MyServer(AsyncA2AServer):
      async def handle_task(self, task):
          ...
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from fastapi import FastAPI
from python_a2a import Task, TaskState as A2ATaskState, TaskStatus

from platform_contracts.a2a import mount_a2a, result_to_task
from platform_contracts.agent_card import AgentCard
from platform_contracts.task_state import TaskState

logger = structlog.get_logger(__name__)

# LangGraph is optional — agents that don't use it (Planner, Gateway) skip it.
try:
    from langgraph.types import Command as LangGraphCommand
except ImportError:  # pragma: no cover
    LangGraphCommand = None  # type: ignore[assignment]


class AsyncA2AServer:
    """A2A Agent 基类。

    子类可选两种模式：
    1. LangGraph 模式：设置 graph / checkpointer / build_input / extract_output
    2. 手动模式：直接 override handle_task(task) → Task
    """

    def __init__(self, card: AgentCard) -> None:
        self.card = card
        # session_id → thread_id 映射（用于 HITL resume）
        self._thread_map: dict[str, str] = {}

    # =========================================================================
    #  LangGraph hooks — 子类 override 这些即可自动获得完整 A2A 生命周期
    # =========================================================================

    @property
    def graph(self):
        """返回 LangGraph CompiledStateGraph。设置后 handle_task 自动委派给 graph。

        None = 不使用 LangGraph（走手动 handle_task override）。
        """
        return None

    @property
    def checkpointer(self):
        """返回 graph 使用的 checkpointer（用于 resume 时获取中断状态）。

        默认 None，即使用 graph 内置的 checkpointer。
        """
        return None

    async def build_input(self, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        """A2A 载荷 → LangGraph 输入状态。子类 override。

        默认：将 payload 序列化为 user message。
        """
        return {"messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}

    async def extract_output(self, graph_state: dict[str, Any]) -> dict[str, Any]:
        """LangGraph 最终状态 → A2A 响应。子类 override。

        默认：返回整个 state（调用方按需取字段）。
        """
        return graph_state

    def _thread_id_for(self, session_id: str) -> str:
        """获取或创建 session_id 对应的 LangGraph thread_id。"""
        if session_id not in self._thread_map:
            self._thread_map[session_id] = f"lg_{session_id}_{uuid.uuid4().hex[:8]}"
        return self._thread_map[session_id]

    # =========================================================================
    #  handle_task — 默认实现：LangGraph 自动生命周期
    # =========================================================================

    async def handle_task(self, task: Task) -> Task:
        """默认实现：如果 self.graph 已设置，自动走 LangGraph 生命周期。

        子类可 override 此方法走手动模式。
        """
        if self.graph is None:
            raise NotImplementedError(
                "Override handle_task() or set self.graph for automatic LangGraph lifecycle"
            )

        payload = self.payload_from_task(task)
        session_id = str(task.session_id or task.id or uuid.uuid4().hex[:12])
        thread_id = self._thread_id_for(session_id)

        try:
            graph_input = await self.build_input(payload, session_id)
        except Exception as exc:
            logger.exception("a2a.build_input_failed", agent=self.card.name, error=str(exc))
            return self.complete_task(task, {"error": str(exc)}, state=TaskState.FAILED)

        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        if self.checkpointer is not None:
            config["configurable"]["checkpointer"] = self.checkpointer

        try:
            result = await self.graph.ainvoke(graph_input, config)
        except Exception as exc:
            # 检测是否为 LangGraph interrupt() 触发的中断
            if self._is_graph_interrupt(exc):
                logger.info(
                    "a2a.graph_interrupted",
                    agent=self.card.name,
                    session_id=session_id,
                    thread_id=thread_id,
                )
                return self._interrupted_task(task, exc, thread_id)
            logger.exception(
                "a2a.graph_invoke_failed",
                agent=self.card.name,
                session_id=session_id,
                error=str(exc),
            )
            return self.complete_task(task, {"error": str(exc)}, state=TaskState.FAILED)

        try:
            output = await self.extract_output(result)
        except Exception as exc:
            logger.exception("a2a.extract_output_failed", agent=self.card.name, error=str(exc))
            output = {"error": str(exc)}

        task = self.complete_task(task, output, state=TaskState.COMPLETED)
        # 清理 thread 映射（已完成，不需要 resume）
        self._thread_map.pop(session_id, None)
        return task

    # =========================================================================
    #  HITL / interrupt 处理
    # =========================================================================

    @staticmethod
    def _is_graph_interrupt(exc: Exception) -> bool:
        """检测异常是否为 LangGraph interrupt()。

        兼容 langgraph >= 0.2 (GraphInterrupt / NodeInterrupt)。
        """
        exc_name = type(exc).__name__
        return exc_name in ("GraphInterrupt", "NodeInterrupt", "Interrupt")

    def _interrupted_task(self, task: Task, exc: Exception, thread_id: str) -> Task:
        """构造 INPUT_REQUIRED 状态的 Task，携带 interrupt 信息供 resume 使用。"""
        interrupt_value = getattr(exc, "value", None) or getattr(exc, "args", [None])[0]
        interrupt_info = {
            "thread_id": thread_id,
            "session_id": task.session_id or task.id,
            "interrupt_value": str(interrupt_value) if interrupt_value else "HITL approval required",
        }
        task.artifacts = [
            {"parts": [{"type": "text", "text": json.dumps(interrupt_info, ensure_ascii=False)}]}
        ]
        task.status = TaskStatus(state=A2ATaskState.INPUT_REQUIRED)
        return task

    async def handle_resume(
        self,
        task_id: str,
        thread_id: str,
        feedback: dict[str, Any],
        _fwd: dict[str, str],
    ) -> tuple[dict[str, Any], str]:
        """HITL resume：将用户反馈注入 graph，从 interrupt 点继续执行。

        返回 (result_dict, task_state)。
        """
        if self.graph is None:
            return {"error": "resume not supported — no graph configured"}, TaskState.FAILED

        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

        # 先获取中断前的状态，验证 thread 确实在等待 resume
        try:
            state = await self.graph.aget_state(config)
        except Exception as exc:
            logger.warning("a2a.resume_get_state_failed", thread_id=thread_id, error=str(exc))
            state = None

        if state is None or not getattr(state, "interrupts", None):
            logger.warning("a2a.resume_no_interrupt_pending", thread_id=thread_id)
            return {"error": "no pending interrupt for this thread"}, TaskState.FAILED

        # 用 LangGraph Command(resume=...) 恢复执行
        resume_input = self._build_resume_command(feedback)

        try:
            result = await self.graph.ainvoke(resume_input, config)
        except Exception as exc:
            if self._is_graph_interrupt(exc):
                # 又有新的 interrupt（多轮 HITL）
                logger.info("a2a.resume_re_interrupted", thread_id=thread_id)
                task = Task(id=task_id, session_id=task_id)
                interrupted = self._interrupted_task(task, exc, thread_id)
                return self._task_result(interrupted), TaskState.INPUT_REQUIRED
            logger.exception("a2a.resume_failed", thread_id=thread_id, error=str(exc))
            return {"error": str(exc)}, TaskState.FAILED

        try:
            output = await self.extract_output(result)
        except Exception as exc:
            logger.exception("a2a.resume_extract_failed", thread_id=thread_id, error=str(exc))
            output = {"error": str(exc)}

        self._thread_map.pop(task_id, None)
        return output, TaskState.COMPLETED

    @staticmethod
    def _build_resume_command(feedback: dict[str, Any]):
        """构造 LangGraph Command(resume=...) 对象。

        提取 feedback 中的 approve/comment/data 字段作为 resume 值。
        """
        resume_value = feedback.get("data") or feedback.get("comment") or feedback.get("approve") or feedback
        if LangGraphCommand is not None:
            return LangGraphCommand(resume=resume_value)
        # LangGraph 未安装时的退化路径
        return {"__resume__": resume_value}

    @staticmethod
    def _task_result(task: Task) -> dict[str, Any]:
        """从 Task artifacts 提取结果 dict。"""
        for art in task.artifacts or []:
            for part in art.get("parts", []):
                if part.get("type") == "text" and part.get("text"):
                    try:
                        return json.loads(part["text"])
                    except json.JSONDecodeError:
                        return {"text": part["text"]}
        return {}

    # =========================================================================
    #  工具方法
    # =========================================================================

    @staticmethod
    def payload_from_task(task: Task) -> dict[str, Any]:
        """从 python_a2a Task 提取载荷 dict。"""
        msg = task.message or {}
        content = msg.get("content") or {}
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    @staticmethod
    def complete_task(
        task: Task,
        result: dict[str, Any],
        *,
        state: str = TaskState.COMPLETED,
    ) -> Task:
        """将 result dict 写入 Task artifacts 并设置状态。"""
        return result_to_task(task, result, state=state)

    # =========================================================================
    #  A2A 协议挂载
    # =========================================================================

    async def _a2a_handler(
        self,
        payload: dict[str, Any],
        _schema: str | None,
        _fwd: dict[str, str],
    ) -> dict[str, Any] | tuple[dict[str, Any], str]:
        task = Task(
            id=payload.get("session_id", "task"),
            message={"role": "user", "content": {"text": json.dumps(payload, ensure_ascii=False)}},
        )
        if payload.get("session_id"):
            task.session_id = str(payload["session_id"])
        out = await self.handle_task(task)
        for art in out.artifacts or []:
            for part in art.get("parts", []):
                if part.get("type") == "text" and part.get("text"):
                    try:
                        return json.loads(part["text"])
                    except json.JSONDecodeError:
                        return {"text": part["text"]}
        st = out.status.state if out.status else A2ATaskState.COMPLETED
        if st == A2ATaskState.INPUT_REQUIRED:
            return {}, TaskState.INPUT_REQUIRED
        if st == A2ATaskState.FAILED:
            return {}, TaskState.FAILED
        return {}

    async def _resume_handler(
        self,
        task_id: str,
        thread_id: str,
        feedback: dict[str, Any],
        fwd: dict[str, str],
    ) -> tuple[dict[str, Any], str]:
        """A2A tasks/resume 的回调——委托给 handle_resume。"""
        return await self.handle_resume(task_id, thread_id, feedback, fwd)

    def mount(self, app: FastAPI, *, prefix: str = "/a2a/v1") -> None:
        """在 FastAPI 上挂载 A2A 端点，含 HITL resume 支持。"""
        mount_a2a(
            app,
            self.card,
            self._a2a_handler,
            prefix=prefix,
            resume_handler=self._resume_handler if self.graph is not None else None,
        )
