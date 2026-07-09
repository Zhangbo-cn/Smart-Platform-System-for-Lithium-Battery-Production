"""knowledge_server MCP 单元测试。"""

from __future__ import annotations

import json
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

# 直接 import（conftest 已添加 path）
from knowledge_server import app as _mod


@pytest.fixture(autouse=True)
def _mock_backends():
    """全局 mock：防止测试连接真实 Milvus/Neo4j。"""
    _mod._neo4j = None
    _mod._milvus = None
    _mod._hybrid = None
    async def _noop(): pass
    # 直接 patch 模块级函数
    with patch.object(_mod, "_ensure_clients", _noop):
        yield


class TestSearchSop:
    @pytest.mark.asyncio
    async def test_no_sop_file(self):
        with patch.object(_mod, "DATA_DIR", Path("/nonexistent")):
            result = await _mod.search_sop(keyword="test")
        parsed = json.loads(result)
        assert "hits" in parsed

    @pytest.mark.asyncio
    async def test_sop_keyword_search(self, tmp_path):
        sop = tmp_path / "sop_snippets.json"
        sop.write_text(json.dumps({"items": [{"title": "涂布操作", "body": "温度控制38°C"}]}))
        with patch.object(_mod, "DATA_DIR", tmp_path):
            result = await _mod.search_sop(keyword="涂布")
        parsed = json.loads(result)
        assert len(parsed["hits"]) >= 1

    @pytest.mark.asyncio
    async def test_health(self):
        result = await _mod.health()
        assert result["status"] == "ok"

    def test_functions_are_callable(self):
        assert inspect.iscoroutinefunction(_mod.search_fmea)
        assert inspect.iscoroutinefunction(_mod.search_sop)
        assert inspect.iscoroutinefunction(_mod.health)
