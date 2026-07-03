from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("lims_server", host="0.0.0.0", port=8104)


@mcp.tool()
async def query_cell_test(cell_barcode: str) -> dict[str, Any]:
    """Fetch end-of-line / formation test results for one cell (capacity, OCV, IR, K)."""
    return {
        "cell_barcode": cell_barcode,
        "capacity_mAh": 4790,
        "ocv_v": 3.42,
        "ir_mohm": 28.5,
        "k_mv_per_day": 0.6,
        "verdict": "NG-LowCapacity",
    }


@mcp.tool()
async def batch_test_summary(batch_id: str) -> dict[str, Any]:
    """Statistical summary of a batch's electrical test results."""
    return {
        "batch_id": batch_id,
        "sample_size": 320,
        "capacity_mean_mAh": 4895,
        "capacity_std_mAh": 35,
        "ng_rate_pct": 1.6,
        "top_ng_reasons": [
            {"reason": "LowCapacity", "count": 4},
            {"reason": "HighIR", "count": 1},
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="sse")
