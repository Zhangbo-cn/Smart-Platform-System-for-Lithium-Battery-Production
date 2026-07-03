from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class GoldenCase:
    case_id: str
    user_query: str
    expected_root_causes: list[str]
    defect_type: str
    notes: str = ""


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    top_k_hit: bool
    confidence: float
    actual_root_cause: str


class EvalRunner:
    """
    Loads golden cases from YAML and replays them against the Agent graph.
    A case passes if any expected root cause appears in the Agent's top-3
    candidates (case-insensitive substring match by default).
    """

    def __init__(self, graph, cases_path: str | Path, top_k: int = 3) -> None:
        self.graph = graph
        self.cases_path = Path(cases_path)
        self.top_k = top_k

    def load_cases(self) -> list[GoldenCase]:
        data = yaml.safe_load(self.cases_path.read_text(encoding="utf-8"))
        return [GoldenCase(**c) for c in data["cases"]]

    async def run(self) -> dict[str, Any]:
        results: list[EvalResult] = []
        for case in self.load_cases():
            state = {
                "trace_id": f"eval_{case.case_id}",
                "user_id": "eval-bot",
                "user_role": "quality_manager",
                "user_query": case.user_query,
            }
            final = await self.graph.ainvoke(state)
            actual = final.get("root_cause", "") or ""
            hit = any(
                exp.lower() in actual.lower() for exp in case.expected_root_causes
            )
            results.append(
                EvalResult(
                    case_id=case.case_id,
                    passed=hit,
                    top_k_hit=hit,
                    confidence=final.get("confidence", 0.0),
                    actual_root_cause=actual,
                )
            )

        passed = sum(1 for r in results if r.passed)
        return {
            "total": len(results),
            "passed": passed,
            "pass_rate": passed / max(len(results), 1),
            "details": [r.__dict__ for r in results],
        }
