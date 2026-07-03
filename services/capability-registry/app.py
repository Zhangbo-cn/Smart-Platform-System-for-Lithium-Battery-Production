"""Capability Registry：AgentCard 种子 + 后台心跳探活（无 LLM，非 Agent）。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from fastapi import FastAPI, HTTPException

from platform_contracts.a2a import mount_a2a
from harness_core.health.probe import probe_http_health
from platform_contracts.agent_registry_seed import ALL_REGISTERED_AGENT_CARDS, CAPABILITY_REGISTRY_CARD
from platform_contracts.task_events import AgentHealthRecord, AgentHealthStatus

logger = structlog.get_logger(__name__)

_CARDS = {c.name: c for c in ALL_REGISTERED_AGENT_CARDS}
_HEALTH: dict[str, AgentHealthRecord] = {}
_PROBE_INTERVAL_SEC = 30.0
_PROBE_TASK: asyncio.Task | None = None


def _list_agents_payload() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in ALL_REGISTERED_AGENT_CARDS:
        h = _HEALTH.get(c.name)
        out.append(
            {
                "name": c.name,
                "enabled": c.enabled,
                "url": c.url,
                "capabilities": c.capabilities,
                "mcp_servers": c.mcp_servers,
                "health_status": h.status.value if h else AgentHealthStatus.UNKNOWN.value,
                "last_probe_at": h.last_probe_at.isoformat() if h and h.last_probe_at else None,
            }
        )
    return out


async def _get_health_record(name: str) -> AgentHealthRecord:
    card = _CARDS.get(name)
    if card is None:
        raise HTTPException(404, f"agent not found: {name}")
    if not card.enabled:
        return AgentHealthRecord(name=name, status=AgentHealthStatus.UNKNOWN, url=card.url, detail="disabled")
    cached = _HEALTH.get(name)
    if cached and cached.last_probe_at:
        age = (datetime.now(timezone.utc) - cached.last_probe_at).total_seconds()
        if age < _PROBE_INTERVAL_SEC:
            return cached
    async with httpx.AsyncClient(timeout=5.0) as client:
        record = await probe_http_health(client, card, previous=cached)
    _HEALTH[name] = record
    return record


async def _probe_all() -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        for card in ALL_REGISTERED_AGENT_CARDS:
            if card.enabled:
                _HEALTH[card.name] = await probe_http_health(client, card, previous=_HEALTH.get(card.name))


async def _registry_a2a_handler(
    payload: dict[str, Any],
    schema: str,
    fwd: dict[str, str],
) -> dict[str, Any]:
    action = payload.get("action", "list")
    if action == "list":
        return {"agents": _list_agents_payload()}
    if action == "get_card":
        name = payload.get("name")
        if not name:
            raise ValueError("name required for get_card")
        card = _CARDS.get(name)
        if card is None:
            raise ValueError(f"agent not found: {name}")
        return card.model_dump()
    if action == "get_health":
        name = payload.get("name")
        if not name:
            raise ValueError("name required for get_health")
        try:
            record = await _get_health_record(name)
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc
        return record.model_dump(mode="json")
    if action == "probe_all":
        await _probe_all()
        return {"health": [r.model_dump(mode="json") for r in _HEALTH.values()]}
    raise ValueError(f"unknown registry action: {action}")


async def _probe_loop() -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            for card in ALL_REGISTERED_AGENT_CARDS:
                if not card.enabled:
                    _HEALTH[card.name] = AgentHealthRecord(
                        name=card.name,
                        status=AgentHealthStatus.UNKNOWN,
                        url=card.url,
                        detail="disabled",
                    )
                    continue
                prev = _HEALTH.get(card.name)
                record = await probe_http_health(client, card, previous=prev)
                _HEALTH[card.name] = record
                if record.status == AgentHealthStatus.DOWN:
                    logger.warning(
                        "registry.agent_down",
                        agent=card.name,
                        failures=record.consecutive_failures,
                        detail=record.detail,
                    )
            await asyncio.sleep(_PROBE_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _PROBE_TASK
    _PROBE_TASK = asyncio.create_task(_probe_loop())
    logger.info("registry.probe_started", interval_sec=_PROBE_INTERVAL_SEC)
    yield
    if _PROBE_TASK:
        _PROBE_TASK.cancel()
        try:
            await _PROBE_TASK
        except asyncio.CancelledError:
            pass


app = FastAPI(title="agent-registry", version="0.2.0", lifespan=lifespan)

mount_a2a(app, CAPABILITY_REGISTRY_CARD, _registry_a2a_handler)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "registry"}


@app.get("/a2a/v1/agents")
def list_agents() -> list[dict]:
    return _list_agents_payload()


@app.get("/a2a/v1/agents/{name}/card")
def get_card(name: str) -> dict:
    card = _CARDS.get(name)
    if card is None:
        raise HTTPException(404, f"agent not found: {name}")
    return card.model_dump()


@app.get("/a2a/v1/agents/{name}/health", response_model=AgentHealthRecord)
async def get_agent_health(name: str) -> AgentHealthRecord:
    return await _get_health_record(name)


@app.post("/a2a/v1/agents/probe")
async def probe_all_agents() -> list[AgentHealthRecord]:
    await _probe_all()
    return list(_HEALTH.values())
