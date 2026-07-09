"""MCP erp_server 单元测试。"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

_mod_path = Path(__file__).resolve().parent.parent / "erp_server.py"
_spec = importlib.util.spec_from_file_location("erp_server_mod", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _tools():
    return [_mod.query_material_batch, _mod.query_recipe]


class TestToolRegistration:
    def test_tool_functions_are_callable(self):
        for fn in _tools():
            assert inspect.iscoroutinefunction(fn)


class TestToolOutput:

    @pytest.mark.asyncio
    async def test_query_material_batch_returns_dict(self):
        result = await _mod.query_material_batch(material_batch_id='MB-001')
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_query_recipe_returns_dict(self):
        result = await _mod.query_recipe(product_code='PC-001')
        assert isinstance(result, dict)

