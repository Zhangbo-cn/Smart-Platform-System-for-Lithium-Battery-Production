"""MCP wms_server 单元测试。"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

_mod_path = Path(__file__).resolve().parent.parent / "wms_server.py"
_spec = importlib.util.spec_from_file_location("wms_server_mod", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _tools():
    return [_mod.get_inventory, _mod.trace_material_location]


class TestToolRegistration:
    def test_tool_functions_are_callable(self):
        for fn in _tools():
            assert inspect.iscoroutinefunction(fn)


class TestToolOutput:

    @pytest.mark.asyncio
    async def test_get_inventory_returns_dict(self):
        result = await _mod.get_inventory(location='WH-A')
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_trace_material_location_returns_dict(self):
        result = await _mod.trace_material_location(material_code='MC-001')
        assert isinstance(result, dict)

