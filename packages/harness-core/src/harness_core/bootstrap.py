from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from harness_core.mcp_client import MCPClient
from harness_core.registry import ToolRegistry, ToolSpec

from harness_core.resilience.retry import with_retry

logger = structlog.get_logger(__name__)

# 仅对 MCP 网络/运行时瞬断重试，不含鉴权失败
_MCP_RETRY_ON = (RuntimeError, ConnectionError, TimeoutError, OSError)


async def bootstrap_mcp_registry(
    registry: ToolRegistry,
    server_urls: dict[str, str],
    allowed_tools: frozenset[str],
    *,
    sensitive_tools: frozenset[str] | None = None,
    role_restricted: dict[str, set[str]] | None = None,
) -> tuple[list[MCPClient], list[str], list[str]]:
    """连接 MCP Server，仅注册白名单 Tool；单 Server 失败不阻塞启动。"""
    clients: list[MCPClient] = []
    connected: list[str] = []
    failed: list[str] = []
    sensitive = sensitive_tools or frozenset()
    restricted = role_restricted or {}

    for server_name, url in server_urls.items():
        client = MCPClient(server_name, url)
        try:
            await client.connect()
        except Exception as exc:
            failed.append(server_name)
            logger.warning("mcp.connect_failed", server=server_name, url=url, error=str(exc))
            continue

        clients.append(client)
        connected.append(server_name)
        registered = 0
        for tool in await client.list_tools():
            full_name = f"{server_name}.{tool['name']}"
            if full_name not in allowed_tools:
                continue
            registry.register(
                ToolSpec(
                    name=full_name,
                    server=server_name,
                    description=tool["description"] or "",
                    parameters=tool["input_schema"],
                    handler=_make_handler(client, tool["name"]),
                    sensitive=full_name in sensitive,
                    required_roles=restricted.get(full_name, set()),
                )
            )
            registered += 1
        logger.info("mcp.tools_registered", server=server_name, count=registered)

    return clients, connected, failed


def _make_handler(client: MCPClient, tool_name: str) -> Callable[[dict[str, Any]], Any]:
    @with_retry(max_attempts=2, base_delay=1.0, retry_on=_MCP_RETRY_ON)
    async def _handler(args: dict[str, Any]) -> Any:
        return await client.call_tool(tool_name, args)

    return _handler
