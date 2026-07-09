"""Client Gateway 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import (
    AssistantTaskRequest,
    HitlResumeRequest,
    should_invoke_planner,
    settings,
)


class TestShouldInvokePlanner:
    def test_skip_planner_flag(self):
        """skip_planner=True 时跳过。"""
        req = AssistantTaskRequest(message="test", skip_planner=True)
        assert should_invoke_planner(req) is False

    def test_auto_plan_off(self):
        """auto_plan=False 时跳过。"""
        with patch.object(settings, "auto_plan", False):
            req = AssistantTaskRequest(message="test")
            assert should_invoke_planner(req) is False

    def test_no_playbook_needs_planner(self):
        """没指定 playbook 时需要 planner。"""
        req = AssistantTaskRequest(message="test", playbook=None)
        assert should_invoke_planner(req) is True

    def test_investigate_without_batch(self):
        """investigate 但没 batch_id 时需要 planner。"""
        req = AssistantTaskRequest(message="test", playbook="investigate", batch_id=None)
        assert should_invoke_planner(req) is True

    def test_investigate_with_batch(self):
        """investigate 且有 batch_id 时不需要 planner。"""
        req = AssistantTaskRequest(message="test", playbook="investigate", batch_id="B001")
        assert should_invoke_planner(req) is False

    def test_rca_never_needs_batch(self):
        """rca 不需要 batch_id。"""
        req = AssistantTaskRequest(message="test", playbook="rca")
        assert should_invoke_planner(req) is False

    def test_trace_without_batch(self):
        """trace_only 但没 batch_id 时需要 planner。"""
        req = AssistantTaskRequest(message="test", playbook="trace_only")
        assert should_invoke_planner(req) is True

    def test_close_loop_with_batch(self):
        """close_loop 且有 batch_id 时不需要 planner。"""
        req = AssistantTaskRequest(message="test", playbook="close_loop", batch_id="B001")
        assert should_invoke_planner(req) is False


class TestRequestModels:
    def test_assistant_task_request_defaults(self):
        req = AssistantTaskRequest(message="测试")
        assert req.playbook is None
        assert req.skip_triage is False
        assert req.confirm_rca is True
        # async_mode is set in the dispatch body, not on the model

    def test_assistant_task_request_full(self):
        req = AssistantTaskRequest(
            message="test",
            playbook="close_loop",
            batch_id="B001",
            factory_id="factory-a",
            defect_type="low_capacity",
            skip_triage=True,
            confirm_rca=False,
            session_id="sess_abc",
        )
        assert req.playbook == "close_loop"
        assert req.batch_id == "B001"

    def test_hitl_resume_request_defaults(self):
        req = HitlResumeRequest()
        assert req.approved is True
        assert req.playbook == "close_loop"

    def test_hitl_resume_with_root_cause(self):
        req = HitlResumeRequest(root_cause="涂布温度异常", thread_id="sess_abc")
        assert req.root_cause == "涂布温度异常"


class TestGatewayAPI:
    @classmethod
    def setup_class(cls):
        from app import app
        cls.client = TestClient(app)

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_create_task_validation(self):
        """message 是必填的。"""
        resp = self.client.post("/v1/assistant/tasks", json={})
        assert resp.status_code == 422

    def test_unknown_route_returns_404(self):
        resp = self.client.get("/nonexistent")
        assert resp.status_code == 404

    def test_a2a_card_endpoint(self):
        """A2A agent.json 端点。"""
        resp = self.client.get("/a2a/v1/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "client-gateway"
