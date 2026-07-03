"""HTTP 健康探针。"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from platform_contracts.agent_card import AgentCard
from platform_contracts.task_events import AgentHealthRecord, AgentHealthStatus


def agent_health_url(card: AgentCard) -> str:
    base = card.url.rstrip("/")
    if base.endswith("/a2a/v1"):
        base = base[: -len("/a2a/v1")]
    return f"{base}/health"


async def probe_http_health(
    client: httpx.AsyncClient,
    card: AgentCard,
    *,
    previous: AgentHealthRecord | None = None,
) -> AgentHealthRecord:
    url = agent_health_url(card)
    now = datetime.now(timezone.utc)
    prev_failures = previous.consecutive_failures if previous else 0
    prev_ok_at = previous.last_ok_at if previous else None
    t0 = time.perf_counter()
    try:
        resp = await client.get(url)
        latency_ms = (time.perf_counter() - t0) * 1000
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code != 200:
            failures = prev_failures + 1
            return AgentHealthRecord(
                name=card.name,
                status=AgentHealthStatus.DOWN if failures >= 3 else AgentHealthStatus.DEGRADED,
                url=url,
                last_probe_at=now,
                last_ok_at=prev_ok_at,
                consecutive_failures=failures,
                latency_ms=latency_ms,
                detail=f"HTTP {resp.status_code}",
            )
        service_status = str(body.get("status", "ok")).lower()
        status = AgentHealthStatus.OK if service_status == "ok" else AgentHealthStatus.DEGRADED
        mode = body.get("mode")
        detail = f"mode={mode}" if mode else None
        return AgentHealthRecord(
            name=card.name,
            status=status,
            url=url,
            last_probe_at=now,
            last_ok_at=now,
            consecutive_failures=0,
            latency_ms=latency_ms,
            detail=detail,
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        failures = prev_failures + 1
        return AgentHealthRecord(
            name=card.name,
            status=AgentHealthStatus.DOWN if failures >= 3 else AgentHealthStatus.DEGRADED,
            url=url,
            last_probe_at=now,
            last_ok_at=prev_ok_at,
            consecutive_failures=failures,
            detail=str(exc),
        )
