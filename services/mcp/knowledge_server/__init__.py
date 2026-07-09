"""知识域 MCP Server：FMEA + SOP + Golden Case 混合检索。"""

from knowledge_server.app import (
    health,
    hybrid_search_golden_case,
    mcp,
    search_fmea,
    search_sop,
)

__all__ = ["mcp", "search_fmea", "hybrid_search_golden_case", "search_sop", "health"]
