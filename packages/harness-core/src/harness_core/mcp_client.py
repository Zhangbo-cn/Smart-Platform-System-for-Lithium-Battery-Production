from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

import structlog
from mcp import ClientSession
from mcp.client.sse import sse_client

logger = structlog.get_logger(__name__)


class MCPClient:
    def __init__(self, server_name: str, sse_url: str) -> None:
        self.server_name = server_name
        self.sse_url = sse_url
        self._session: ClientSession | None = None
        self._stack = AsyncExitStack()

    async def connect(self) -> None:
        read, write = await self._stack.enter_async_context(sse_client(self.sse_url))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        logger.info("mcp.connected", server=self.server_name, url=self.sse_url)

    async def list_tools(self) -> list[dict[str, Any]]:
        assert self._session
        resp = await self._session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in resp.tools
        ]

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        assert self._session
        resp = await self._session.call_tool(name, args)
        if resp.isError:
            raise RuntimeError(f"MCP tool error [{name}]: {resp.content}")
        return [c.model_dump() for c in resp.content]

    async def close(self) -> None:
        await self._stack.aclose()
