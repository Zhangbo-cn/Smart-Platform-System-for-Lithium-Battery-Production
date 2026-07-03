"""RCA bootstrap 白名单：单一事实源 platform-contracts.mcp_tool_matrix。"""

from __future__ import annotations

from platform_contracts.mcp_tool_matrix import allowed_tools_for

_AGENT = "quality-rca-agent"


def rca_allowed_tools() -> frozenset[str]:
    # knowledge.* 为进程内 FMEA，不经 MCP bootstrap
    return frozenset(
        t for t in allowed_tools_for(_AGENT) if not t.startswith("knowledge.")
    )
