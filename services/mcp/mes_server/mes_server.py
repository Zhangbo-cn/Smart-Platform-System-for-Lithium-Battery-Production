from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mes_server", host="0.0.0.0", port=8101)


@mcp.tool()
async def query_batch_trace(
    cell_barcode: str | None = None,
    batch_id: str | None = None,
    process_steps: list[str] | None = None,
) -> dict[str, Any]:
    """Query the full station-by-station production trace for a cell or batch."""
    return {
        "batch_id": batch_id or f"B-{datetime.utcnow():%Y%m%d}",
        "cell_barcode": cell_barcode,
        "stations": [
            {"step": "mixing", "equipment": "MIX-01", "operator": "U001", "ts": "2026-05-29T08:10:00"},
            {"step": "coating", "equipment": "COAT-A2", "operator": "U002", "ts": "2026-05-29T09:30:00"},
            {"step": "rolling", "equipment": "ROLL-03", "operator": "U002", "ts": "2026-05-29T11:00:00"},
        ],
    }


@mcp.tool()
async def query_defect_cells(
    start_time: str,
    end_time: str,
    defect_type: str,
    line_id: str,
) -> dict[str, Any]:
    """List defective cells within a window for a given defect type and line."""
    return {
        "line_id": line_id,
        "defect_type": defect_type,
        "window": [start_time, end_time],
        "cells": [
            {"barcode": "C240529A001", "batch_id": "B20260529-A1", "capacity_mAh": 4820, "ng_reason": defect_type},
            {"barcode": "C240529A002", "batch_id": "B20260529-A1", "capacity_mAh": 4795, "ng_reason": defect_type},
        ],
        "count": 2,
    }


@mcp.tool()
async def get_process_params(
    batch_id: str,
    process_step: str,
    param_names: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch process parameters of a batch at a given step (e.g. coating thickness, roll pressure)."""
    return {
        "batch_id": batch_id,
        "process_step": process_step,
        "params": {
            "coating_thickness_mean_um": 95.2,
            "coating_thickness_std_um": 3.8,
            "coating_speed_m_min": 28.5,
            "drying_zone_temp_c": [85, 95, 105, 95, 85],
        },
    }


if __name__ == "__main__":
    mcp.run(transport="sse")
