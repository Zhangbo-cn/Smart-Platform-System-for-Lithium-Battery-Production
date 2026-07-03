"""A2A JSON-RPC 传输层：基于 python_a2a Task 协议，FastAPI async 服务端 + httpx async 客户端。

Agent 互调专用，不属于 harness-core（MCP / RBAC / 审计）。

用法：
  mount_a2a(app, card, handler)          # 服务端
  A2AClient(http, headers=...).send(...) # 客户端
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request
from python_a2a import (
    AgentCard as A2AAgentCard,
    AgentSkill as A2AAgentSkill,
    Message,
    MessageRole,
    Task,
    TaskState as A2ATaskState,
    TaskStatus,
    TextContent,
)

from platform_contracts.agent_card import AgentCard
from platform_contracts.task_state import TaskState

logger = structlog.get_logger(__name__)

TaskHandler = Callable[..., Awaitable[Any]]
ResumeHandler = Callable[[str, str, dict[str, Any], dict[str, str]], Awaitable[Any]]
TaskLookup = Callable[[str, dict[str, str]], Awaitable[dict[str, Any] | None]]

_STATE_TO_A2A = {
    TaskState.SUBMITTED: A2ATaskState.SUBMITTED,
    TaskState.RUNNING: A2ATaskState.WAITING,
    TaskState.INPUT_REQUIRED: A2ATaskState.INPUT_REQUIRED,
    TaskState.COMPLETED: A2ATaskState.COMPLETED,
    TaskState.FAILED: A2ATaskState.FAILED,
}


class A2AError(Exception):
    """下游 agent 返回 JSON-RPC error 或不可达。"""


def result_to_task(task: Task, result: dict[str, Any], *, state: str = TaskState.COMPLETED) -> Task:
    task.artifacts = [{"parts": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}]
    a2a_state = _STATE_TO_A2A.get(TaskState(state), A2ATaskState.UNKNOWN)
    task.status = TaskStatus(state=a2a_state)
    return task


def _to_a2a_card(card: AgentCard) -> A2AAgentCard:
    return A2AAgentCard(
        name=card.name,
        description=card.description,
        url=card.url,
        version=card.version,
        skills=[
            A2AAgentSkill(name=s.name, description=s.description or "", id=s.id, tags=[])
            for s in card.skills
        ],
    )


def _payload_to_task(payload: dict[str, Any], *, session_id: str, schema: str) -> Task:
    msg = Message(
        content=TextContent(text=json.dumps(payload, ensure_ascii=False)),
        role=MessageRole.USER,
    )
    task = Task(id=session_id, session_id=session_id, message=msg.to_dict())
    task.metadata = {"schema": schema}
    return task


def _task_to_payload(task: Task) -> tuple[dict[str, Any], str | None]:
    schema = (task.metadata or {}).get("schema") if task.metadata else None
    msg = task.message or {}
    text = _extract_text(msg)
    return (json.loads(text) if text else {}), schema


def _task_result(task: Task) -> dict[str, Any]:
    for art in task.artifacts or []:
        for part in art.get("parts", []):
            if part.get("type") == "text" and part.get("text"):
                try:
                    return json.loads(part["text"])
                except json.JSONDecodeError:
                    return {"text": part["text"]}
    return {}


def _extract_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict) and content.get("text"):
        return content["text"]
    for part in message.get("parts", []) or []:
        if isinstance(part, dict) and part.get("text"):
            return part["text"]
    if isinstance(content, str):
        return content
    return ""


def _forward_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "x-trace-id")}


def _normalize_out(out: Any) -> tuple[dict[str, Any], str]:
    if isinstance(out, tuple):
        result, state = out
        return result, str(state)
    return out, TaskState.COMPLETED


def mount_a2a(
    app: FastAPI,
    card: AgentCard,
    handler: TaskHandler,
    *,
    prefix: str = "/a2a/v1",
    resume_handler: ResumeHandler | None = None,
    task_lookup: TaskLookup | None = None,
) -> None:
    """在 FastAPI 应用上挂 A2A JSON-RPC 端点（agent.json / tasks/send / tasks/resume）。"""
    card_json = _to_a2a_card(card).to_dict()

    @app.get(f"{prefix}/.well-known/agent.json")
    async def agent_card() -> dict[str, Any]:
        return card_json

    @app.post(f"{prefix}/tasks/send")
    async def tasks_send(request: Request) -> dict[str, Any]:
        body = await request.json()
        req_id = body.get("id", 1)
        try:
            task = Task.from_dict(body.get("params") or {})
            payload, schema = _task_to_payload(task)
            out = await handler(payload, schema, _forward_headers(request))
            result, state = _normalize_out(out)
            result_to_task(task, result, state=state)
            return {"jsonrpc": "2.0", "id": req_id, "result": task.to_dict()}
        except Exception as exc:  # noqa: BLE001
            logger.exception("a2a.tasks_send_failed", agent=card.name, error=str(exc))
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(exc)}}

    if resume_handler is not None:

        @app.post(f"{prefix}/tasks/resume")
        async def tasks_resume(request: Request) -> dict[str, Any]:
            body = await request.json()
            req_id = body.get("id", 1)
            params = body.get("params") or {}
            task_id = str(params.get("task_id", ""))
            thread_id = str(params.get("thread_id", ""))
            feedback = params.get("feedback") or {}
            if not task_id or not thread_id:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "task_id and thread_id required"},
                }
            try:
                task = Task(id=task_id, session_id=task_id)
                out = await resume_handler(task_id, thread_id, feedback, _forward_headers(request))
                result, state = _normalize_out(out)
                result_to_task(task, result, state=state)
                return {"jsonrpc": "2.0", "id": req_id, "result": task.to_dict()}
            except Exception as exc:  # noqa: BLE001
                logger.exception("a2a.tasks_resume_failed", agent=card.name, error=str(exc))
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(exc)}}

    if task_lookup is not None:

        @app.get(f"{prefix}/tasks/{{task_id}}")
        async def tasks_get(task_id: str, request: Request) -> dict[str, Any]:
            found = await task_lookup(task_id, _forward_headers(request))
            if found is None:
                raise HTTPException(404, f"task not found: {task_id}")
            return found


class A2AClient:
    """async httpx 客户端，报文与 python_a2a.A2AClient 互通。"""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        headers: dict[str, str] | None = None,
        prefix: str = "/a2a/v1",
    ) -> None:
        self._http = http
        self._headers = headers or {}
        self._prefix = prefix

    async def send(
        self,
        agent_url: str,
        payload: dict[str, Any],
        *,
        session_id: str,
        schema: str,
    ) -> dict[str, Any]:
        task = _payload_to_task(payload, session_id=session_id, schema=schema)
        data = await self._rpc(agent_url, "tasks/send", task.to_dict())
        if data.get("error"):
            raise A2AError(f"{schema}@{agent_url}: {data['error'].get('message')}")
        return _task_result(Task.from_dict(data.get("result", {})))

    async def get_task(
        self,
        agent_url: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        """GET /tasks/{task_id} 查询任务状态（非 JSON-RPC，直接 HTTP GET）。"""
        base = agent_url.rstrip("/")
        url = f"{base}{self._prefix}/tasks/{task_id}"
        resp = await self._http.get(url, headers=self._headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def resume(
        self,
        agent_url: str,
        *,
        task_id: str,
        thread_id: str,
        feedback: dict[str, Any],
    ) -> dict[str, Any]:
        params = {"task_id": task_id, "thread_id": thread_id, "feedback": feedback}
        data = await self._rpc(agent_url, "tasks/resume", params)
        if data.get("error"):
            raise A2AError(f"resume@{agent_url}: {data['error'].get('message')}")
        return _task_result(Task.from_dict(data.get("result", {})))

    async def _rpc(self, agent_url: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        base = agent_url.rstrip("/")
        endpoint = f"{base}{self._prefix}/{method}"
        resp = await self._http.post(
            endpoint,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()
