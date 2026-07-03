"""薄 Agent 统一 MCP bootstrap + A2A 注册（读 platform-contracts 矩阵）。"""

from __future__ import annotations

import httpx
import structlog

from harness_core.bootstrap import bootstrap_mcp_registry
from harness_core.mcp_client import MCPClient
from harness_core.registry import ToolRegistry
from platform_contracts.mcp_tool_matrix import (
    MCP_SERVER_TOOLS,
    allowed_tools_for,
    servers_for_agent,
    tool_policies_for,
)

logger = structlog.get_logger(__name__)


def _load_exclusive_tools() -> dict[str, str]:
    """从 mcp_tool_matrix 加载独占工具映射。"""
    exclusive: dict[str, str] = {}
    for server, tools in MCP_SERVER_TOOLS.items():
        for tool_name, meta in tools.items():
            owner = meta.get("exclusive_agent")
            if owner:
                exclusive[f"{server}.{tool_name}"] = str(owner)
    return exclusive


async def bootstrap_agent_tools(
    agent_name: str,
    registry: ToolRegistry,
    server_url_by_name: dict[str, str],
) -> tuple[list[MCPClient], list[str], list[str]]:
    # 注入独占工具白名单（安全门闩运行时强制执行）
    from harness_core.permission.checker import PermissionChecker
    PermissionChecker.EXCLUSIVE_TOOLS.update(_load_exclusive_tools())

    servers = servers_for_agent(agent_name)
    urls = {name: server_url_by_name[name] for name in servers if name in server_url_by_name}
    allowed = frozenset(allowed_tools_for(agent_name))
    sensitive, role_restricted = tool_policies_for(agent_name)
    return await bootstrap_mcp_registry(
        registry,
        urls,
        allowed,
        sensitive_tools=sensitive,
        role_restricted=role_restricted,
    )


async def register_with_registry(
    registry_url: str,
    agent_name: str,
    agent_description: str,
    agent_url: str,
    capabilities: list[str],
    *,
    version: str = "1.0.0",
    timeout: float = 5.0,
) -> bool:
    """向 Capability Registry 注册当前 Agent。

    用于 Agent 启动后在 lifespan 中调用。注册成功后 Registry 会定期发心跳探测。
    """
    import json

    payload = {
        "name": agent_name,
        "description": agent_description,
        "url": agent_url.rstrip("/"),
        "version": version,
        "capabilities": capabilities,
        "skills": [
            {
                "id": cap,
                "name": cap,
                "description": f"{agent_name} capability: {cap}",
            }
            for cap in capabilities
        ],
        "enabled": True,
    }
    try:
        url = f"{registry_url.rstrip('/')}/registry/register"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info("registry.registered", agent=agent_name, url=registry_url)
            return True
    except Exception as exc:
        logger.warning("registry.register_failed", agent=agent_name, error=str(exc))
        return False
