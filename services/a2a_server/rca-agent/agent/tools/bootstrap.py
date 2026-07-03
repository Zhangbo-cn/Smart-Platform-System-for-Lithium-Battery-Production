from __future__ import annotations

from agent.tools.allowlist import rca_allowed_tools
from agent.tools.registry import ToolRegistry
from config import get_settings
from harness_core.bootstrap import bootstrap_mcp_registry
from platform_contracts.mcp_tool_matrix import tool_policies_for

_AGENT = "quality-rca-agent"


async def bootstrap_registry(registry: ToolRegistry):
    settings = get_settings()
    sensitive, role_restricted = tool_policies_for(_AGENT)
    return await bootstrap_mcp_registry(
        registry,
        {
            "mes": settings.mcp_mes_url,
            "scada": settings.mcp_scada_url,
            "erp": settings.mcp_erp_url,
            "lims": settings.mcp_lims_url,
        },
        rca_allowed_tools(),
        sensitive_tools=sensitive,
        role_restricted=role_restricted,
    )
