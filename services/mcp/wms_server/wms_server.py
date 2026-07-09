"""WMS MCP Server：仓储管理（wms-supply-agent 消费）。"""

from __future__ import annotations

from datetime import timezone, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("wms_server", host="0.0.0.0", port=8108)


@mcp.tool()
async def get_inventory(
    material_code: str | None = None,
    location: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """库存查询。"""
    return {
        "material_code": material_code or "NMC-811",
        "batch_id": batch_id,
        "location": location or "WH-A-03",
        "quantity_kg": 2500.0,
        "available_kg": 1800.0,
        "lot_number": "L20260610-NMC-A",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


@mcp.tool()
async def trace_material_location(
    material_code: str,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """物料库位追溯。"""
    return {
        "material_code": material_code,
        "batch_id": batch_id or "B20260629-A1",
        "movements": [
            {"ts": "2026-06-29T08:00:00", "from": "WH-IN", "to": "WH-A-03", "qty_kg": 500},
            {"ts": "2026-06-29T14:00:00", "from": "WH-A-03", "to": "LINE-1-MIX", "qty_kg": 200},
        ],
        "current_location": "WH-A-03",
        "current_qty_kg": 300,
    }


if __name__ == "__main__":
    mcp.run(transport="sse")
