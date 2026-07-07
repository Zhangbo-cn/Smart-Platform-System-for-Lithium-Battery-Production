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
from collections.abc import Awaitable, Callable
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

    def __init__(
        self,
        card: AgentCard,
        *,
        redis_url: str | None = None,
        redis_ttl: int = 86_400,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.card = card
        self._redis_url = redis_url
        self._redis_ttl = redis_ttl
        self._event_callback = event_callback
        # session_id → thread_id 映射（用于 HITL resume）
        # 当 redis_url 设置时使用 Redis，否则用内存 dict
        self._thread_map: dict[str, str] = {}
        self._redis: object | None = None  # lazy init

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

    async def _ensure_redis(self) -> object | None:
        """惰性初始化 Redis 连接。"""
        if self._redis is not None:
            return self._redis
        if not self._redis_url:
            return None
        try:
            import redis.asyncio as redis_mod  # type: ignore[import-untyped]

            self._redis = await redis_mod.from_url(self._redis_url, decode_responses=True)
            return self._redis
        except Exception:
            logger.warning("a2a.redis_unavailable", url=self._redis_url)
            self._redis = False  # 标记不可用，下次跳过
            return None

    async def _redis_get_thread(self, session_id: str) -> str | None:
        r = await self._ensure_redis()
        if r is None:
            return None
        try:
            val = await r.get(f"thread_map:{session_id}")  # type: ignore[union-attr]
            return val
        except Exception:
            return None

    async def _redis_set_thread(self, session_id: str, thread_id: str) -> None:
        r = await self._ensure_redis()
        if r is None:
            return
        try:
            await r.setex(f"thread_map:{session_id}", self._redis_ttl, thread_id)  # type: ignore[union-attr]
        except Exception:
            pass

    async def _redis_del_thread(self, session_id: str) -> None:
        r = await self._ensure_redis()
        if r is None:
            return
        try:
            await r.delete(f"thread_map:{session_id}")  # type: ignore[union-attr]
        except Exception:
            pass

    def _thread_id_for(self, session_id: str) -> str:
        """获取或创建 session_id 对应的 LangGraph thread_id（内存快捷路径）。"""
        if session_id not in self._thread_map:
            self._thread_map[session_id] = f"lg_{session_id}_{uuid.uuid4().hex[:8]}"
        return self._thread_map[session_id]

    async def _athread_id_for(self, session_id: str) -> str:
        """获取 session_id 对应的 thread_id。内存优先，Redis 兜底恢复。

        重启后首次调用：查 Redis → 未命中则新建。
        """
        if session_id in self._thread_map:
            return self._thread_map[session_id]
        # 尝试从 Redis 恢复（服务器重启后 session 仍在等待 HITL）
        tid = await self._redis_get_thread(session_id)
        if tid:
            self._thread_map[session_id] = tid
            return tid
        # 新建
        tid = f"lg_{session_id}_{uuid.uuid4().hex[:8]}"
        self._thread_map[session_id] = tid
        await self._redis_set_thread(session_id, tid)
        return tid

    async def _aclear_thread(self, session_id: str) -> None:
        """清理 session 的 thread 映射（内存 + Redis）。"""
        self._thread_map.pop(session_id, None)
        await self._redis_del_thread(session_id)

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
        thread_id = await self._athread_id_for(session_id)

        try:
            graph_input = await self.build_input(payload, session_id)
        except Exception as exc:
            logger.exception("a2a.build_input_failed", agent=self.card.name, error=str(exc))
            return self.complete_task(task, {"error": str(exc)}, state=TaskState.FAILED)

        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        if self.checkpointer is not None:
            config["configurable"]["checkpointer"] = self.checkpointer

        # ---- streaming 分支 ----
        is_stream = bool((task.metadata or {}).get("stream"))
        if is_stream and self.graph is not None and hasattr(self.graph, "astream_events"):
            logger.info(
                "a2a.graph_streaming",
                agent=self.card.name, session_id=session_id, thread_id=thread_id,
            )
            try:
                last_state = None
                async for event in self.graph.astream_events(graph_input, config, version="v2"):
                    if self._event_callback:
                        await self._event_callback(event)
                    if event.get("event") == "on_chain_end" and "output" in event.get("data", {}):
                        last_state = event["data"]["output"]
                if last_state is None:
                    raise RuntimeError("streaming finished without final state")
                result = last_state
            except Exception as exc:
                if self._is_graph_interrupt(exc):
                    logger.info("a2a.graph_interrupted", agent=self.card.name, session_id=session_id, thread_id=thread_id)
                    return self._interrupted_task(task, exc, thread_id)
                logger.exception("a2a.graph_stream_failed", agent=self.card.name, error=str(exc))
                return self.complete_task(task, {"error": str(exc)}, state=TaskState.FAILED)
        else:
            # ---- 非 streaming：原 graph.ainvoke ----
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

        # ---- __interrupt__ 返回值检测（兼容新版 LangGraph 不抛异常的模式） ----
        if isinstance(result, dict) and result.get("__interrupt__"):
            logger.info(
                "a2a.graph_interrupted_via_result",
                agent=self.card.name, session_id=session_id, thread_id=thread_id,
            )

            class _MockInterrupt:
                value = result["__interrupt__"]

            return self._interrupted_task(task, _MockInterrupt(), thread_id)

        try:
            output = await self.extract_output(result)
        except Exception as exc:
            logger.exception("a2a.extract_output_failed", agent=self.card.name, error=str(exc))
            output = {"error": str(exc)}

        task = self.complete_task(task, output, state=TaskState.COMPLETED)
        # 清理 thread 映射（已完成，不需要 resume）
        await self._aclear_thread(session_id)
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

        await self._aclear_thread(task_id)
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
