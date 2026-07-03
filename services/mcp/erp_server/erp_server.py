from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("erp_server", host="0.0.0.0", port=8103)


@mcp.tool()
async def query_material_batch(material_batch_id: str) -> dict[str, Any]:
    """Look up a raw-material batch's supplier, COA, and inbound inspection record."""
    return {
        "material_batch_id": material_batch_id,
        "supplier": "Supplier-X",
        "material": "NCM811-PowderA",
        "iqc_pass": True,
        "incoming_date": "2026-05-25",
    }


@mcp.tool()
async def query_recipe(product_code: str) -> dict[str, Any]:
    """Sensitive: BOM and mixing recipe for a given product code (RBAC restricted)."""
    return {
        "product_code": product_code,
        "bom": [
            {"material": "NCM811", "ratio_pct": 96.0},
            {"material": "PVDF", "ratio_pct": 2.0},
            {"material": "ConductiveAgent", "ratio_pct": 2.0},
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="sse")
