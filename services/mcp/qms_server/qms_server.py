from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("qms_server", host="0.0.0.0", port=8105)

_CAPA_STORE: dict[str, dict[str, Any]] = {}


@mcp.tool()
async def create_8d_draft(
    session_id: str,
    title: str,
    report_md: str,
    root_cause: str,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Create an 8D/CAPA draft in QMS from a finalized quality report."""
    capa_id = f"CAPA-{session_id.replace('sess_', '')[:10].upper()}"
    record = {
        "capa_id": capa_id,
        "session_id": session_id,
        "title": title,
        "root_cause": root_cause,
        "batch_id": batch_id,
        "status": "draft",
        "report_md": report_md,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    _CAPA_STORE[capa_id] = record
    return {"capa_id": capa_id, "status": "draft", "qms_record_id": capa_id}


@mcp.tool()
async def update_capa_status(
    capa_id: str,
    status: str,
    comment: str = "",
) -> dict[str, Any]:
    """Update CAPA workflow status (e.g. draft → pending_approval → closed)."""
    record = _CAPA_STORE.get(capa_id)
    if record is None:
        return {"capa_id": capa_id, "status": status, "updated": False, "error": "not_found"}
    record["status"] = status
    record["comment"] = comment
    record["updated_at"] = datetime.utcnow().isoformat() + "Z"
    return {"capa_id": capa_id, "status": status, "updated": True}


if __name__ == "__main__":
    mcp.run(transport="sse")
