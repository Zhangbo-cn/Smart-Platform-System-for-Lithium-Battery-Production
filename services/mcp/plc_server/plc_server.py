"""PLC MCP Server：产线控制与安全门闩（Safety-Agent 独占）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("plc_server", host="0.0.0.0", port=8110)


@mcp.tool()
async def emergency_stop(
    line_id: str,
    reason: str,
    operator_id: str,
) -> dict[str, Any]:
    """紧急停线；需 Safety-Agent HITL 签核后调用。"""
    return {
        "action": "emergency_stop",
        "line_id": line_id,
        "reason": reason,
        "operator_id": operator_id,
        "ts": datetime.utcnow().isoformat(),
        "status": "stopped",
        "affected_equipment": [f"{line_id}-COAT", f"{line_id}-ROLL", f"{line_id}-SLIT"],
        "safety_interlock": "engaged",
    }


@mcp.tool()
async def write_setpoint(
    line_id: str,
    equipment_id: str,
    parameter: str,
    value: float,
    operator_id: str,
    reason: str,
) -> dict[str, Any]:
    """写 PLC 设定值（温度/速度/压力等）；需 Safety-Agent HITL 签核。"""
    return {
        "action": "write_setpoint",
        "line_id": line_id,
        "equipment_id": equipment_id,
        "parameter": parameter,
        "value": value,
        "operator_id": operator_id,
        "reason": reason,
        "ts": datetime.utcnow().isoformat(),
        "previous_value": 0.0,  # stub
        "status": "applied",
    }


if __name__ == "__main__":
    mcp.run(transport="sse")
