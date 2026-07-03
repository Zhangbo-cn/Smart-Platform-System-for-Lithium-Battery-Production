from __future__ import annotations

from typing import Any


class ContextCompressor:
    def __init__(self, max_rows: int = 100, max_text_chars: int = 4000) -> None:
        self.max_rows = max_rows
        self.max_text_chars = max_text_chars

    def compress(self, payload: Any) -> Any:
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return self._compress_table(payload)
        if isinstance(payload, str) and len(payload) > self.max_text_chars:
            head = payload[: self.max_text_chars // 2]
            tail = payload[-self.max_text_chars // 2 :]
            return f"{head}\n...[truncated {len(payload) - self.max_text_chars} chars]...\n{tail}"
        if isinstance(payload, dict):
            return {k: self.compress(v) for k, v in payload.items()}
        return payload

    def _compress_table(self, rows: list[dict]) -> dict:
        if len(rows) <= self.max_rows:
            return {"rows": rows, "row_count": len(rows), "compressed": False}
        sample = rows[: self.max_rows // 2] + rows[-self.max_rows // 2 :]
        numeric_keys = {k for k in rows[0] if isinstance(rows[0][k], (int, float))}
        stats = {}
        for k in numeric_keys:
            values = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
            if values:
                stats[k] = {
                    "min": min(values),
                    "max": max(values),
                    "mean": sum(values) / len(values),
                    "count": len(values),
                }
        return {
            "rows": sample,
            "row_count": len(rows),
            "stats": stats,
            "compressed": True,
            "note": f"sampled {len(sample)} of {len(rows)} rows",
        }
