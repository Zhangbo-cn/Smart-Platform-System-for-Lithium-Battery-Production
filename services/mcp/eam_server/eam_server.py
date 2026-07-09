"""EAM MCP Server：设备资产管理（equipment-health-agent 消费）。"""

from __future__ import annotations

from datetime import timezone, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("eam_server", host="0.0.0.0", port=8107)


@mcp.tool()
async def get_maintenance_log(
    equipment_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    """查询设备维保记录。"""
    return {
        "equipment_id": equipment_id,
        "window": [start_time or "2026-01-01", end_time or datetime.now(timezone.utc).isoformat()],
        "logs": [
            {
                "date": "2026-06-15",
                "type": "preventive",
                "description": "涂布机月度保养：更换刮刀、校准厚度传感器",
                "technician": "T003",
                "parts_replaced": ["doctor_blade", "thickness_sensor_cal"],
            },
            {
                "date": "2026-06-01",
                "type": "corrective",
                "description": "干燥区温控偏差修复",
                "technician": "T005",
                "parts_replaced": ["heater_element_Z3"],
            },
        ],
        "count": 2,
    }


@mcp.tool()
async def get_work_orders(
    equipment_id: str,
    status: str | None = None,
) -> dict[str, Any]:
    """查询设备工单列表。"""
    return {
        "equipment_id": equipment_id,
        "status_filter": status or "all",
        "orders": [
            {"id": "WO-2026-0615", "type": "maintenance", "status": "closed", "priority": "P2"},
            {"id": "WO-2026-0620", "type": "inspection", "status": "open", "priority": "P1"},
        ],
        "count": 2,
    }


if __name__ == "__main__":
    mcp.run(transport="sse")
