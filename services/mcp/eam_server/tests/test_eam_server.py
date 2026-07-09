"""MCP eam_server 单元测试。"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

_mod_path = Path(__file__).resolve().parent.parent / "eam_server.py"
_spec = importlib.util.spec_from_file_location("eam_server_mod", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _tools():
    return [_mod.get_maintenance_log, _mod.get_work_orders]


class TestToolRegistration:
    def test_tool_functions_are_callable(self):
        for fn in _tools():
            assert inspect.iscoroutinefunction(fn)


class TestToolOutput:

    @pytest.mark.asyncio
    async def test_get_maintenance_log_returns_dict(self):
        result = await _mod.get_maintenance_log(equipment_id='E001')
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_work_orders_returns_dict(self):
        result = await _mod.get_work_orders(equipment_id='E001')
        assert isinstance(result, dict)

