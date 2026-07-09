"""MCP plc_server 单元测试。"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

_mod_path = Path(__file__).resolve().parent.parent / "plc_server.py"
_spec = importlib.util.spec_from_file_location("plc_server_mod", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _tools():
    return [_mod.emergency_stop, _mod.write_setpoint]


class TestToolRegistration:
    def test_tool_functions_are_callable(self):
        for fn in _tools():
            assert inspect.iscoroutinefunction(fn)


class TestToolOutput:

    @pytest.mark.asyncio
    async def test_emergency_stop_returns_dict(self):
        result = await _mod.emergency_stop(line_id='L1', reason='测试', operator_id='u1')
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_write_setpoint_returns_dict(self):
        result = await _mod.write_setpoint(line_id='L1', equipment_id='E001', parameter='temp', value=38.0, operator_id='u1', reason='调整')
        assert isinstance(result, dict)

