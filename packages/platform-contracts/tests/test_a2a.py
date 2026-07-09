"""A2A 协议层单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from python_a2a import Task, TaskState as A2ATaskState, TaskStatus

from platform_contracts.a2a import (
    A2AClient,
    A2AError,
    _extract_text,
    _normalize_out,
    _payload_to_task,
    _task_result,
    _task_to_payload,
    _to_a2a_card,
    result_to_task,
)
from platform_contracts.agent_card import AgentCard, AgentSkill
from platform_contracts.task_state import TaskState


# ── JSON-RPC 序列化/反序列化 ─────────────────────────────


class TestPayloadRoundTrip:
    def test_payload_to_task(self):
        payload = {"batch_id": "B001", "user_query": "test"}
        task = _payload_to_task(payload, session_id="sess_1", schema="TestSchema")
        assert task.id == "sess_1"
        assert task.session_id == "sess_1"
        assert task.metadata["schema"] == "TestSchema"

    def test_task_to_payload(self):
        payload = {"batch_id": "B001"}
        task = _payload_to_task(payload, session_id="sess_2", schema="RcaRequest")
        extracted, schema = _task_to_payload(task)
        assert extracted == payload
        assert schema == "RcaRequest"

    def test_round_trip_preserves_params(self):
        original = {"id": "abc", "type": "capacity", "value": 98.5}
        task = _payload_to_task(original, session_id="sess_3", schema="Test")
        extracted, _ = _task_to_payload(task)
        assert extracted == original


# ── 文本提取 ─────────────────────────────────────────────


class TestExtractText:
    def test_content_dict_with_text(self):
        msg = {"content": {"text": "hello"}}
        assert _extract_text(msg) == "hello"

    def test_parts_list_with_text(self):
        msg = {"parts": [{"type": "text", "text": "from parts"}]}
        assert _extract_text(msg) == "from parts"

    def test_content_is_str(self):
        msg = {"content": "plain string"}
        assert _extract_text(msg) == "plain string"

    def test_empty_message(self):
        assert _extract_text({}) == ""

    def test_content_text_fallback(self):
        msg = {"content": {"text": "primary"}, "parts": [{"type": "text", "text": "fallback"}]}
        assert _extract_text(msg) == "primary"


# ── result_to_task ────────────────────────────────────────


class TestResultToTask:
    def test_sets_artifacts(self):
        task = Task(id="t1", session_id="s1")
        result_to_task(task, {"root_cause": "涂布温度异常"}, state=TaskState.COMPLETED)
        assert task.artifacts is not None
        assert len(task.artifacts) >= 1

    def test_state_mapping(self):
        task = Task(id="t2", session_id="s2")
        result_to_task(task, {}, state=TaskState.COMPLETED)
        assert task.status.state == A2ATaskState.COMPLETED

    def test_input_required_mapping(self):
        task = Task(id="t3", session_id="s3")
        result_to_task(task, {"requires_hitl": True}, state=TaskState.INPUT_REQUIRED)
        assert task.status.state == A2ATaskState.INPUT_REQUIRED

    def test_failed_mapping(self):
        task = Task(id="t4", session_id="s4")
        result_to_task(task, {"error": "something broke"}, state=TaskState.FAILED)
        assert task.status.state == A2ATaskState.FAILED


# ── _task_result ──────────────────────────────────────────


class TestTaskResult:
    def test_extracts_json_from_artifacts(self):
        task = Task(id="t1", session_id="s1")
        data = {"root_cause": "test"}
        result_to_task(task, data)
        assert _task_result(task) == data

    def test_returns_empty_for_no_artifacts(self):
        task = Task(id="t2", session_id="s2")
        assert _task_result(task) == {}

    def test_fallback_to_text(self):
        task = Task(id="t3", session_id="s3")
        from python_a2a import Message, MessageRole, TextContent

        task.artifacts = [{"parts": [{"type": "text", "text": "plain text"}]}]
        result = _task_result(task)
        assert result.get("text") == "plain text"


# ── _normalize_out ────────────────────────────────────────


class TestNormalizeOut:
    def test_tuple_with_state(self):
        result, state = _normalize_out(({"key": "val"}, TaskState.INPUT_REQUIRED))
        assert result == {"key": "val"}
        assert state == TaskState.INPUT_REQUIRED

    def test_single_dict_defaults_to_completed(self):
        result, state = _normalize_out({"key": "val"})
        assert result == {"key": "val"}
        assert state == TaskState.COMPLETED


# ── _to_a2a_card ──────────────────────────────────────────


class TestToA2ACard:
    def test_converts_skills(self):
        card = AgentCard(
            name="test-agent",
            description="test",
            url="http://localhost:8000/a2a/v1",
            capabilities=["test"],
            skills=[
                AgentSkill(id="s1", name="skill1", description="does x"),
            ],
        )
        a2a_card = _to_a2a_card(card)
        assert a2a_card.name == "test-agent"
        assert len(a2a_card.skills) == 1
        assert a2a_card.skills[0].name == "skill1"

    def test_minimal_card(self):
        card = AgentCard(
            name="minimal",
            description="min",
            url="http://localhost/a2a/v1",
            capabilities=[],
        )
        a2a_card = _to_a2a_card(card)
        assert a2a_card.name == "minimal"
        assert a2a_card.skills == []


# ── A2AClient ─────────────────────────────────────────────


class TestA2AClient:
    @pytest.mark.asyncio
    async def test_send_success(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        # mock POST response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        # build a valid Task JSON response
        task = Task(id="sess_1", session_id="sess_1")
        from python_a2a import TaskStatus, TaskState as A2AST

        task.status = TaskStatus(state=A2AST.COMPLETED)
        from python_a2a import TextContent

        task.artifacts = [{"parts": [{"type": "text", "text": '{"result": "ok"}'}]}]
        mock_resp.json = MagicMock(return_value={
            "jsonrpc": "2.0",
            "id": 1,
            "result": task.to_dict(),
        })
        mock_http.post = AsyncMock(return_value=mock_resp)

        client = A2AClient(mock_http)
        result = await client.send(
            "http://localhost:8003", {"query": "test"},
            session_id="sess_1", schema="Test",
        )
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_send_raises_on_error(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "upstream error"},
        })
        mock_http.post = AsyncMock(return_value=mock_resp)

        client = A2AClient(mock_http)
        with pytest.raises(A2AError, match="upstream error"):
            await client.send(
                "http://localhost:8003", {},
                session_id="sess_1", schema="Test",
            )

    @pytest.mark.asyncio
    async def test_get_task_found(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"id": "sess_1", "status": "completed"})
        mock_http.get = AsyncMock(return_value=mock_resp)

        client = A2AClient(mock_http)
        result = await client.get_task("http://localhost:8003", "sess_1")
        assert result["id"] == "sess_1"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_http.get = AsyncMock(return_value=mock_resp)

        client = A2AClient(mock_http)
        result = await client.get_task("http://localhost:8003", "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_resume_success(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        task = Task(id="sess_1", session_id="sess_1")
        from python_a2a import TaskStatus, TaskState as A2AST

        task.status = TaskStatus(state=A2AST.COMPLETED)
        from python_a2a import TextContent

        task.artifacts = [{"parts": [{"type": "text", "text": '{"approved": true}'}]}]
        mock_resp.json = MagicMock(return_value={
            "jsonrpc": "2.0", "id": 1, "result": task.to_dict(),
        })
        mock_http.post = AsyncMock(return_value=mock_resp)

        client = A2AClient(mock_http)
        result = await client.resume(
            "http://localhost:8003", task_id="sess_1",
            thread_id="sess_1", feedback={"approved": True},
        )
        assert result == {"approved": True}

    @pytest.mark.asyncio
    async def test_resume_raises_on_error(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32000, "message": "resume failed"},
        })
        mock_http.post = AsyncMock(return_value=mock_resp)

        client = A2AClient(mock_http)
        with pytest.raises(A2AError, match="resume failed"):
            await client.resume(
                "http://localhost:8003", task_id="sess_1",
                thread_id="sess_1", feedback={},
            )
