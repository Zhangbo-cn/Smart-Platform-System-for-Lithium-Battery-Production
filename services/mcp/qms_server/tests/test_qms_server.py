"""MCP qms_server 单元测试。"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

_mod_path = Path(__file__).resolve().parent.parent / "qms_server.py"
_spec = importlib.util.spec_from_file_location("qms_server_mod", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _tools():
    return [_mod.create_8d_draft, _mod.update_capa_status]


class TestToolRegistration:
    def test_tool_functions_are_callable(self):
        for fn in _tools():
            assert inspect.iscoroutinefunction(fn)


class TestToolOutput:

    @pytest.mark.asyncio
    async def test_create_8d_draft_returns_dict(self):
        result = await _mod.create_8d_draft(session_id='s1', title='test', report_md='# 8D', root_cause='温度异常')
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_update_capa_status_returns_dict(self):
        result = await _mod.update_capa_status(capa_id='CAPA-001', status='closed', comment='done')
        assert isinstance(result, dict)

