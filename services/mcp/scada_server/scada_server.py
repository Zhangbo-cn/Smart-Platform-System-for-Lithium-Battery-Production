from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("scada_server", host="0.0.0.0", port=8102)


@mcp.tool()
async def query_equipment_timeseries(
    equipment_id: str,
    sensor_tags: list[str],
    start_time: str,
    end_time: str,
    aggregation: str = "1min",
) -> dict[str, Any]:
    """Read aggregated sensor time series (temperature, pressure, speed, tension)."""
    return {
        "equipment_id": equipment_id,
        "aggregation": aggregation,
        "series": {
            tag: {
                "min": 80.1, "max": 102.4, "mean": 92.3, "p95": 100.1,
                "samples": 60,
            }
            for tag in sensor_tags
        },
        "window": [start_time, end_time],
    }


@mcp.tool()
async def detect_anomaly_window(
    equipment_id: str,
    start_time: str,
    end_time: str,
    method: str = "3sigma",
) -> dict[str, Any]:
    """Detect anomaly time-windows on the given equipment using stats or a pretrained model."""
    return {
        "equipment_id": equipment_id,
        "method": method,
        "anomalies": [
            {"start": "2026-05-29T10:12:00", "end": "2026-05-29T10:18:00",
             "tag": "drying_zone_3_temp", "severity": "high"},
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="sse")
