"""Reporter Golden Case 离线评测。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval_core.judge import rule_judge_reporter_d4_locked


@dataclass
class ReporterGoldenCase:
    case_id: str
    root_cause: str
    defect_type: str
    min_report_chars: int = 200


def load_reporter_cases(path: str | Path) -> list[ReporterGoldenCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [ReporterGoldenCase(**c) for c in data["cases"]]


def evaluate_report(case: ReporterGoldenCase, report_md: str) -> dict[str, Any]:
    verdict = rule_judge_reporter_d4_locked(report_md, case.root_cause)
    length_ok = len(report_md) >= case.min_report_chars
    passed = verdict.passed and length_ok
    return {
        "case_id": case.case_id,
        "passed": passed,
        "d4_locked": verdict.passed,
        "length_ok": length_ok,
        "score": verdict.score if length_ok else 0.0,
    }
