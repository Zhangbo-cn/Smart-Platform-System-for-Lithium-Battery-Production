"""MCP lims_server 单元测试。"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

_mod_path = Path(__file__).resolve().parent.parent / "lims_server.py"
_spec = importlib.util.spec_from_file_location("lims_server_mod", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _tools():
    return [_mod.query_cell_test, _mod.batch_test_summary]


class TestToolRegistration:
    def test_tool_functions_are_callable(self):
        for fn in _tools():
            assert inspect.iscoroutinefunction(fn)


class TestToolOutput:

    @pytest.mark.asyncio
    async def test_query_cell_test_returns_dict(self):
        result = await _mod.query_cell_test(cell_barcode='C001')
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_batch_test_summary_returns_dict(self):
        result = await _mod.batch_test_summary(batch_id='B001')
        assert isinstance(result, dict)

