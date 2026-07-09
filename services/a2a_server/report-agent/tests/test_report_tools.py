"""Reporter Agent 进程内工具测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from report_tools import (
    bind_report_context,
    get_locked_root_cause,
    get_rca_artifacts,
    search_sop,
    search_golden_case,
)


class TestLockedRootCause:
    def test_bind_and_get(self):
        bind_report_context({"root_cause": "涂布温度异常"})
        result = get_locked_root_cause.invoke({})
        assert "涂布温度异常" in result

    def test_empty_default(self):
        bind_report_context({})
        result = get_locked_root_cause.invoke({})
        assert result == ""

    def test_context_isolation(self):
        bind_report_context({"root_cause": "第一轮"})
        assert "第一轮" in get_locked_root_cause.invoke({})
        bind_report_context({"root_cause": "第二轮"})
        assert "第二轮" in get_locked_root_cause.invoke({})
        assert "第一轮" not in get_locked_root_cause.invoke({})


class TestRcaArtifacts:
    def test_get_artifacts(self):
        bind_report_context({
            "rca_artifacts": {
                "summary": "分析摘要",
                "evidence_count": 3,
            }
        })
        raw = get_rca_artifacts.invoke({})
        parsed = json.loads(raw)
        assert parsed["summary"] == "分析摘要"
        assert parsed["evidence_count"] == 3

    def test_empty_artifacts(self):
        bind_report_context({})
        raw = get_rca_artifacts.invoke({})
        parsed = json.loads(raw)
        assert parsed == {}


class TestSearchSop:
    @pytest.mark.asyncio
    async def test_search_without_file(self):
        result = await search_sop.ainvoke({"defect_type": "coating"})
        parsed = json.loads(result)
        assert "hits" in parsed


class TestSearchGoldenCase:
    @pytest.mark.asyncio
    async def test_search_without_file(self):
        result = await search_golden_case.ainvoke({"defect_type": "coating"})
        parsed = json.loads(result)
        assert "hits" in parsed
