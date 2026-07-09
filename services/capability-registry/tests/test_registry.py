"""Capability Registry 单元测试。"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


class TestRegistryAPI:
    @classmethod
    def setup_class(cls):
        with patch("app._PROBE_TASK", None):
            from app import app
            cls.client = TestClient(app)

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_a2a_card_endpoint(self):
        resp = self.client.get("/a2a/v1/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "capability-registry"

    def test_list_agents_returns_list(self):
        resp = self.client.get("/a2a/v1/agents")
        assert resp.status_code == 200
        agents = resp.json()
        assert isinstance(agents, list)
        # 应包含种子卡片
        names = [a["name"] for a in agents]
        assert "quality-rca-agent" in names
        assert "client-gateway" in names

    def test_get_card_found(self):
        resp = self.client.get("/a2a/v1/agents/quality-rca-agent/card")
        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == "quality-rca-agent"

    def test_get_card_not_found(self):
        resp = self.client.get("/a2a/v1/agents/nonexistent/card")
        assert resp.status_code == 404


class TestRegistryEndpoints:
    """直接测试 REST 端点（绕过 lifespan）。"""

    def setup_method(self):
        from app import app as _app
        self.client = TestClient(_app)

    def test_register_and_list(self):
        """注册新 Agent 后能在列表中找到。"""
        resp = self.client.post("/registry/register", json={
            "name": "test-agent",
            "url": "http://localhost:9999/a2a/v1",
            "capabilities": ["test"],
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "registered"

        # 验证在列表中
        resp = self.client.get("/registry/agents")
        agents = resp.json()
        names = [a["name"] for a in agents]
        assert "test-agent" in names

    def test_heartbeat_updates_status(self):
        """心跳后状态更新。"""
        self.client.post("/registry/register", json={
            "name": "hb-agent",
            "url": "http://localhost:9998/a2a/v1",
        })
        resp = self.client.post("/registry/heartbeat", json={
            "url": "http://localhost:9998/a2a/v1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_unregister_removes_agent(self):
        """注销后 Agent 从动态列表消失。"""
        self.client.post("/registry/register", json={
            "name": "remove-me",
            "url": "http://localhost:9997/a2a/v1",
        })
        resp = self.client.post("/registry/unregister", json={
            "url": "http://localhost:9997/a2a/v1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "unregistered"

        # 验证不再在动态列表中（但种子卡片还在）
        resp = self.client.get("/registry/agents")
        names = [a["name"] for a in resp.json()]
        assert "remove-me" not in names

    def test_unregister_unknown(self):
        """注销未注册的 Agent 返回 not_found。"""
        resp = self.client.post("/registry/unregister", json={
            "url": "http://nonexistent/a2a/v1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"

    def test_heartbeat_unknown(self):
        """未知 Agent 的心跳返回 unknown。"""
        resp = self.client.post("/registry/heartbeat", json={
            "url": "http://unknown/a2a/v1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "unknown"


class TestPruning:
    def test_stale_agent_pruned(self):
        """超时未心跳的 Agent 被摘除。"""
        from app import _DYNAMIC_CARDS, _LAST_HEARTBEAT, _prune_stale_agents

        # 注册一个 Agent
        _DYNAMIC_CARDS["stale-test"] = type("Card", (), {
            "name": "stale-test",
            "description": "test",
            "url": "http://stale",
            "version": "1.0",
            "capabilities": [],
            "skills": [],
            "mcp_servers": [],
            "enabled": True,
        })()  # type: ignore

        # 心跳时间设为很久以前
        _LAST_HEARTBEAT["stale-test"] = time.time() - 600  # 10分钟前

        import asyncio
        asyncio.run(_prune_stale_agents())

        assert "stale-test" not in _DYNAMIC_CARDS
