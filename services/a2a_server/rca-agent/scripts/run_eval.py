"""Run golden-case regression against the quality analysis graph.

Usage:
    python -m scripts.run_eval
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent.graphs import build_quality_analysis_graph
from agent.tools.bootstrap import bootstrap_registry
from agent.tools.registry import ToolRegistry
from harness.eval import EvalRunner


async def main() -> None:
    registry = ToolRegistry()
    clients, _, _ = await bootstrap_registry(registry)
    try:
        graph = build_quality_analysis_graph(registry)
        runner = EvalRunner(graph, Path(__file__).parent.parent / "knowledge" / "golden_cases.yaml")
        report = await runner.run()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        for c in clients:
            await c.close()


if __name__ == "__main__":
    asyncio.run(main())
