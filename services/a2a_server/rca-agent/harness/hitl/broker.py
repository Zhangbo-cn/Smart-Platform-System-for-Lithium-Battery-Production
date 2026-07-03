from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class HITLRequest:
    request_id: str
    trace_id: str
    user_id: str
    title: str
    payload: dict[str, Any]


@dataclass
class HITLResponse:
    request_id: str
    approved: bool
    feedback: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class HITLBroker:
    """
    Bridges Agent <-> human reviewers. The Agent waits on a Future keyed by
    request_id; an external channel (IM bot webhook) calls resolve() with the
    reviewer's decision. Production deployments back this with Postgres so
    pending requests survive process restarts.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[HITLResponse]] = {}
        self._thread_by_request: dict[str, str] = {}
        self._request_by_thread: dict[str, str] = {}
        self._payload_by_request: dict[str, dict[str, Any]] = {}

    async def request(
        self,
        trace_id: str,
        user_id: str,
        title: str,
        payload: dict[str, Any],
        timeout: float = 1800.0,
    ) -> HITLResponse:
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[HITLResponse] = loop.create_future()
        self._pending[request_id] = fut

        await self._dispatch(
            HITLRequest(
                request_id=request_id, trace_id=trace_id,
                user_id=user_id, title=title, payload=payload,
            )
        )

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    def register_pending(
        self,
        thread_id: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> str:
        """Track a LangGraph interrupt awaiting human review."""
        request_id = uuid.uuid4().hex
        self._thread_by_request[request_id] = thread_id
        self._request_by_thread[thread_id] = request_id
        self._payload_by_request[request_id] = payload
        logger.info(
            "hitl.registered",
            request_id=request_id,
            thread_id=thread_id,
            trace_id=trace_id,
        )
        return request_id

    def get_thread_id(self, request_id: str) -> str | None:
        return self._thread_by_request.get(request_id)

    def get_request_id(self, thread_id: str) -> str | None:
        return self._request_by_thread.get(thread_id)

    def get_payload(self, request_id: str) -> dict[str, Any]:
        return self._payload_by_request.get(request_id, {})

    def clear_pending(self, thread_id: str) -> None:
        request_id = self._request_by_thread.pop(thread_id, None)
        if request_id:
            self._thread_by_request.pop(request_id, None)
            self._payload_by_request.pop(request_id, None)
            self._pending.pop(request_id, None)

    def resolve(self, response: HITLResponse) -> bool:
        fut = self._pending.get(response.request_id)
        if not fut or fut.done():
            return False
        fut.set_result(response)
        thread_id = self._thread_by_request.get(response.request_id)
        if thread_id:
            self.clear_pending(thread_id)
        return True

    async def _dispatch(self, req: HITLRequest) -> None:
        logger.info(
            "hitl.dispatch", request_id=req.request_id,
            trace_id=req.trace_id, user_id=req.user_id, title=req.title,
        )
