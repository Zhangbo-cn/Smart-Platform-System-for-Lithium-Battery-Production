"""SSE 格式化。"""

from __future__ import annotations

import json
from typing import Any


def format_sse(event: str, data: dict[str, Any], *, event_id: str | int | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    lines.append("")
    return "\n".join(lines) + "\n"
