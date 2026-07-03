"""Capability Registry：AgentCard 动态注册 + 心跳探活（无 LLM，非 Agent）。

支持两种模式：
- 静态模式（默认）：从 agent_registry_seed.py 加载种子卡片
- 动态模式：Agent 启动时通过 POST /registry/register 注册，
           通过 POST /registry/heartbeat 发送心跳，
           5 分钟无心跳自动摘除
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from platform_contracts.a2a import mount_a2a
from harness_core.health.probe import probe_http_health
from platform_contracts.agent_card import AgentCard
from platform_contracts.agent_registry_seed import ALL_REGISTERED_AGENT_CARDS, CAPABILITY_REGISTRY_CARD
from platform_contracts.task_events import AgentHealthRecord, AgentHealthStatus

logger = structlog.get_logger(__name__)

# 静态种子卡片（启动时加载）
_STATIC_CARDS = {c.name: c for c in ALL_REGISTERED_AGENT_CARDS}
# 动态注册的卡片（运行时添加，覆盖静态）
_DYNAMIC_CARDS: dict[str, AgentCard] = {}
_HEALTH: dict[str, AgentHealthRecord] = {}
# 心跳追踪：name -> last_heartbeat_timestamp
_LAST_HEARTBEAT: dict[str, float] = {}
_PRUNING_LOCK = asyncio.Lock()
_HEARTBEAT_TIMEOUT_SEC = 300.0  # 5 分钟无心跳自动摘除
_PROBE_INTERVAL_SEC = 30.0
_PROBE_TASK: asyncio.Task | None = None
_PRUNING_TASK: asyncio.Task | None = None


def _all_cards() -> dict[str, AgentCard]:
    """合并静态 + 动态卡片（动态覆盖静态）。"""
    cards = dict(_STATIC_CARDS)
    cards.update(_DYNAMIC_CARDS)
    return cards


class RegisterRequest(BaseModel):
    """Agent 注册请求体。"""
    name: str
    description: str = ""
    url: str
    version: str = "1.0.0"
    capabilities: list[str] = []
    skills: list[dict[str, Any]] = []
    mcp_servers: list[str] = []
    enabled: bool = True


class HeartbeatRequest(BaseModel):
    """Agent 心跳请求体。"""
    url: str


def _list_agents_payload() -> list[dict[str, Any]]:
    cards = _all_cards()
    out: list[dict[str, Any]] = []
    for name, c in cards.items():
        h = _HEALTH.get(name)
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
    cards = _all_cards()
    card = cards.get(name)
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
    cards = _all_cards()
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, card in cards.items():
            if card.enabled:
                _HEALTH[name] = await probe_http_health(client, card, previous=_HEALTH.get(name))


async def _registry_a2a_handler(
    payload: dict[str, Any],
    schema: str,
    fwd: dict[str, str],
) -> dict[str, Any]:
    action = payload.get("action", "list")
    cards = _all_cards()
    if action == "list":
        return {"agents": _list_agents_payload()}
    if action == "get_card":
        name = payload.get("name")
        if not name:
            raise ValueError("name required for get_card")
        card = cards.get(name)
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


async def _prune_stale_agents() -> None:
    """摘除超过 HEARTBEAT_TIMEOUT_SEC 未发送心跳的动态 Agent。"""
    now = time.time()
    stale = []
    async with _PRUNING_LOCK:
        for name, last_ts in list(_LAST_HEARTBEAT.items()):
            if name not in _DYNAMIC_CARDS:
                continue
            if now - last_ts > _HEARTBEAT_TIMEOUT_SEC:
                stale.append(name)
                _DYNAMIC_CARDS.pop(name, None)
                _LAST_HEARTBEAT.pop(name, None)
                _HEALTH.pop(name, None)
    for name in stale:
        logger.warning("registry.pruned_stale_agent", agent=name, timeout_sec=_HEARTBEAT_TIMEOUT_SEC)


async def _probe_loop() -> None:
    probe_count = 0
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            cards = _all_cards()
            for name, card in cards.items():
                if not card.enabled:
                    _HEALTH[name] = AgentHealthRecord(
                        name=name,
                        status=AgentHealthStatus.UNKNOWN,
                        url=card.url,
                        detail="disabled",
                    )
                    continue
                prev = _HEALTH.get(name)
                record = await probe_http_health(client, card, previous=prev)
                _HEALTH[name] = record
                if record.status == AgentHealthStatus.DOWN:
                    logger.warning(
                        "registry.agent_down",
                        agent=name,
                        failures=record.consecutive_failures,
                        detail=record.detail,
                    )
            # 每 5 轮清理一次失联 Agent
            probe_count += 1
            if probe_count % 5 == 0:
                await _prune_stale_agents()
            await asyncio.sleep(_PROBE_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _PROBE_TASK, _PRUNING_TASK
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
    cards = _all_cards()
    card = cards.get(name)
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


# ── 动态注册端点（兼容 python_a2a DiscoveryClient 模式）──


@app.post("/registry/register")
async def register_agent(req: RegisterRequest) -> dict:
    card = AgentCard(
        name=req.name,
        description=req.description,
        url=req.url,
        version=req.version,
        capabilities=req.capabilities,
        skills=[
            AgentSkill(id=s.get("id", s.get("name", "")), name=s.get("name", ""), description=s.get("description"))
            for s in req.skills
        ],
        mcp_servers=req.mcp_servers,
        enabled=req.enabled,
    )
    _DYNAMIC_CARDS[req.name] = card
    _LAST_HEARTBEAT[req.name] = time.time()
    logger.info("registry.agent_registered", agent=req.name, url=req.url)
    return {"status": "registered", "name": req.name, "url": req.url}


@app.post("/registry/unregister")
async def unregister_agent(req: HeartbeatRequest) -> dict:
    name_to_remove = None
    for name, card in list(_DYNAMIC_CARDS.items()):
        if card.url == req.url or card.url.rstrip("/") == req.url.rstrip("/"):
            name_to_remove = name
            break
    if name_to_remove:
        _DYNAMIC_CARDS.pop(name_to_remove, None)
        _LAST_HEARTBEAT.pop(name_to_remove, None)
        _HEALTH.pop(name_to_remove, None)
        logger.info("registry.agent_unregistered", agent=name_to_remove, url=req.url)
        return {"status": "unregistered", "url": req.url}
    return {"status": "not_found", "url": req.url, "note": "not a dynamically registered agent"}


@app.post("/registry/heartbeat")
async def heartbeat_agent(req: HeartbeatRequest) -> dict:
    for name, card in _all_cards().items():
        if card.url == req.url or card.url.rstrip("/") == req.url.rstrip("/"):
            _LAST_HEARTBEAT[name] = time.time()
            if name in _DYNAMIC_CARDS:
                _HEALTH[name] = AgentHealthRecord(
                    name=name,
                    status=AgentHealthStatus.UP,
                    url=card.url,
                    detail="heartbeat received",
                )
            return {"status": "ok", "name": name}
    return {"status": "unknown", "url": req.url, "note": "agent not registered"}


@app.get("/registry/agents")
async def list_registered_agents() -> list[dict]:
    return [c.model_dump() for c in _all_cards().values()]
