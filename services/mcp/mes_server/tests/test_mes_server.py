"""MCP mes_server 单元测试。"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

_mod_path = Path(__file__).resolve().parent.parent / "mes_server.py"
_spec = importlib.util.spec_from_file_location("mes_server_mod", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _tools():
    return [_mod.query_batch_trace, _mod.query_defect_cells, _mod.get_process_params, _mod.get_shift_summary]


class TestToolRegistration:
    def test_tool_functions_are_callable(self):
        for fn in _tools():
            assert inspect.iscoroutinefunction(fn)


class TestToolOutput:
    @pytest.mark.asyncio
    async def test_query_batch_trace(self):
        r = await _mod.query_batch_trace(batch_id="B001")
        assert "stations" in r

    @pytest.mark.asyncio
    async def test_query_defect_cells(self):
        r = await _mod.query_defect_cells(start_time="2026-01-01", end_time="2026-12-31", defect_type="low_capacity", line_id="L1")
        assert "cells" in r

    @pytest.mark.asyncio
    async def test_get_process_params(self):
        r = await _mod.get_process_params(batch_id="B001", process_step="coating")
        assert "params" in r

    @pytest.mark.asyncio
    async def test_get_shift_summary(self):
        r = await _mod.get_shift_summary(line_id="L1")
        assert "production_qty" in r
