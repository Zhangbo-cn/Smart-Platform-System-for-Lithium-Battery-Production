"""MCP scada_server 单元测试。"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

_mod_path = Path(__file__).resolve().parent.parent / "scada_server.py"
_spec = importlib.util.spec_from_file_location("scada_server_mod", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _tools():
    return [_mod.query_equipment_timeseries, _mod.detect_anomaly_window]


class TestToolRegistration:
    def test_tool_functions_are_callable(self):
        for fn in _tools():
            assert inspect.iscoroutinefunction(fn)


class TestToolOutput:

    @pytest.mark.asyncio
    async def test_query_equipment_timeseries_returns_dict(self):
        result = await _mod.query_equipment_timeseries(equipment_id='E001', sensor_tags=['temp'], start_time='2026-07-09T00:00:00', end_time='2026-07-09T23:59:59', aggregation='1min')
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_detect_anomaly_window_returns_dict(self):
        result = await _mod.detect_anomaly_window(equipment_id='E001', start_time='2026-07-09T00:00:00', end_time='2026-07-09T23:59:59', method='3sigma')
        assert isinstance(result, dict)

