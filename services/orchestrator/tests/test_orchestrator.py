"""Orchestrator 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestOrchestratorAPI:
    @classmethod
    def setup_class(cls):
        """初始化 TestClient（模块内 mock settings 避免外部依赖）。"""
        with patch.multiple(
            "app",
            _playbook_engine=MagicMock(),
            _discovered_cards=[],
            _sessions=MagicMock(),
        ):
            from app import app
            cls.client = TestClient(app)

    def test_health_endpoint(self):
        """健康检查应返回状态。"""
        resp = self.client.get("/health")
        assert resp.status_code == 200

    def test_unknown_route_returns_404(self):
        resp = self.client.get("/nonexistent")
        assert resp.status_code == 404

    def test_a2a_card_endpoint(self):
        """A2A agent.json 端点。"""
        resp = self.client.get("/a2a/v1/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "playbook-orchestrator"

    def test_dispatch_basic_validation(self):
        """dispatch 端点基本可达。"""
        resp = self.client.post(
            "/a2a/v1/router/dispatch",
            json={"playbook": "investigate", "message": "test", "batch_id": "B001"},
        )
        # 因为 mock 的 engine 不完整，预期会返回 500
        # 但关键是路由可达，不走 404/422 以外的状态码
        assert resp.status_code != 404


class TestOrchestratorSettings:
    def test_settings_defaults(self):
        """验证默认配置合理。"""
        from app import OrchestratorSettings

        s = OrchestratorSettings()
        # http_retries 默认值跟随 settings 定义
        assert s.context_backend == "memory"
        assert s.enable_smart_routing is False


class TestDispatchActions:
    def test_platform_context_creation(self):
        """PlatformContext 创建和基本写入。"""
        from platform_contracts.platform_context import PlatformContext

        ctx = PlatformContext(
            session_id="sess_test",
            trace_id="trc_test",
            batch_id="B001",
            defect_type="low_capacity",
        )
        assert ctx.session_id == "sess_test"
        assert ctx.batch_id == "B001"
        # severity default 是 None，由 triage 赋值

        # 写入 rca 结果
        ctx.rca.root_cause = "涂布温度异常"
        ctx.rca.confidence = 0.85
        assert ctx.rca.root_cause == "涂布温度异常"

    def test_platform_context_serialization(self):
        """PlatformContext 可以序列化为 dict。"""
        from platform_contracts.platform_context import PlatformContext

        ctx = PlatformContext(session_id="s", trace_id="t")
        d = ctx.model_dump(mode="json")
        assert d["session_id"] == "s"
        assert d["trace_id"] == "t"
        assert "rca" in d
        assert "report_8d" in d

    def test_fallback_rca_output(self):
        """兜底 RCA 返回置信度 0.3 + 强制 HITL。"""
        from app import _fallback_rca
        from platform_contracts.platform_context import PlatformContext

        ctx = PlatformContext(
            session_id="sess_fb",
            trace_id="trc_fb",
            defect_type="low_capacity",
        )
        result = _fallback_rca(ctx)
        assert result["confidence"] == 0.3
        assert result["requires_hitl"] is True
        assert "must" in result["root_cause"] or "必须" in result["root_cause"]


class TestTriageStub:
    def test_resolve_triage_by_keyword(self):
        from platform_contracts.triage_stub import resolve_triage

        result = resolve_triage(query="B001 容量低", batch_id="B001")
        assert result.defect_type is not None
        assert result.stub is True

    def test_resolve_triage_with_given_defect(self):
        from platform_contracts.triage_stub import resolve_triage

        result = resolve_triage(
            query="随便看看",
            defect_type="ir_high",
        )
        assert result.defect_type == "ir_high"

    def test_resolve_triage_unknown(self):
        from platform_contracts.triage_stub import resolve_triage

        result = resolve_triage(query="今天天气怎么样")
        assert result.defect_type is not None  # 有兜底
