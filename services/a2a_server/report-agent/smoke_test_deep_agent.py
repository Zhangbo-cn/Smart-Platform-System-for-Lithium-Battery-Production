"""Quick smoke test: 验证 Deep Agent 模式能否正常生成 8D 报告."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# 强制切换 CWD 到 report-agent 目录，确保 .env 能被 pydantic-settings 读到
_REPORT_DIR = Path(__file__).resolve().parent
os.chdir(str(_REPORT_DIR))
sys.path.insert(0, str(_REPORT_DIR))

from deep_agent_runner import run_report_with_deep_agent
from platform_contracts.agent_handoffs import Report8dRequest
from settings import get_settings


async def main():
    settings = get_settings()
    print(f"LLM: {settings.llm_base_url} model={settings.llm_model}")
    print(f"Mode: {settings.reporter_mode}")
    print(f"API Key: {'***' + settings.llm_api_key[-8:] if settings.llm_api_key else 'MISSING'}")

    # Mock ToolRegistry: 内部 tools 不需要 registry，MCP tools 会走 mock
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=[{"text": json.dumps({"capa_id": "mock-capa-001"})}])

    # 构造一个真实的 RCA 输出
    req = Report8dRequest(
        session_id="test-session-deep-agent-001",
        factory_id="BATTERY-P01",
        defect_type="容量衰减",
        root_cause="涂布工序 → 面密度不均匀 → 局部容量偏低",
        confidence=0.72,
        hitl_approved=True,
        recommendations=[
            "调整涂布机 3# 模头间隙至 0.18mm",
            "对辊压后极片增加面密度在线检测频次",
        ],
        evidence=[
            {"description": "涂布面密度 CPK=0.82（<1.33）", "source_tool": "mes.query_spc", "confidence": 0.85},
            {"description": "3# 模头压力波动 ±0.12MPa（上限 0.10）", "source_tool": "scada.read_pressure", "confidence": 0.78},
            {"description": "容量测试：32 支中 5 支 < 标称 95%", "source_tool": "lims.query_test", "confidence": 0.92},
        ],
        rca_artifacts={
            "summary": "涂布面密度不均匀导致容量衰减，3#模头压力波动为直接原因",
            "root_cause": "涂布工序 → 面密度不均匀 → 局部容量偏低",
            "confidence": 0.72,
            "evidence_count": 3,
            "defect_type": "容量衰减",
        },
    )

    print("\n--- Starting Deep Agent 8D generation ---")
    report_md, recs, capa_id, qms_status, mode = await run_report_with_deep_agent(
        registry, req, settings
    )

    print(f"\nMode: {mode}")
    print(f"CAPA ID: {capa_id}")
    print(f"QMS Status: {qms_status}")
    print(f"Recommendations ({len(recs)}):")
    for r in recs:
        print(f"  - {r}")
    print(f"\n--- 8D Report ({len(report_md)} chars) ---")
    # Write to file to avoid Windows GBK encoding issues
    out_path = _REPORT_DIR / "test_output_8d.md"
    out_path.write_text(report_md, encoding="utf-8")
    print(f"Report saved to: {out_path}")
    # Print ASCII-safe preview
    print(report_md.encode("ascii", errors="replace").decode("ascii")[:2000])
    if len(report_md) > 2000:
        print(f"\n... ({len(report_md) - 2000} more chars)")

    if mode == "deep_agent":
        print("\n[OK] Deep Agent mode works! Sub-agents generated D4/D5/D6 via LLM")
    else:
        print(f"\n[WARN] Fell back to template mode -- check LLM config or deepagents import")


if __name__ == "__main__":
    asyncio.run(main())
