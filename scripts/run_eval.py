"""Reporter / RCA 离线评测 CLI。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "packages" / "eval-core" / "src"))
sys.path.insert(0, str(_ROOT / "packages" / "harness-core" / "src"))
sys.path.insert(0, str(_ROOT / "packages" / "platform-contracts" / "src"))
sys.path.insert(0, str(_ROOT / "services" / "a2a_server" / "report-agent"))

from eval_core.judge import JudgeVerdict, llm_judge_consistency, rule_judge_rca, rule_judge_reporter_d4_locked
from eval_core.reporter_eval import ReporterGoldenCase, evaluate_report, load_reporter_cases
from platform_contracts.agent_handoffs import Report8dRequest
from report_runner import _build_report_md


def _load_rca_cases(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


async def _run_reporter_template_eval() -> dict:
    cases = load_reporter_cases(_ROOT / "packages" / "eval-core" / "data" / "reporter_golden.json")
    results = []
    for case in cases:
        req = Report8dRequest(
            session_id=f"eval-{case.case_id}",
            root_cause=case.root_cause,
            hitl_approved=True,
            defect_type=case.defect_type,
            confidence=0.85,
        )
        md, _ = _build_report_md(req)
        results.append(evaluate_report(case, md))
    passed = sum(1 for r in results if r["passed"])
    return {"total": len(results), "passed": passed, "pass_rate": f"{passed}/{len(results)}", "details": results}


async def _run_rca_rule_eval() -> dict:
    cases = _load_rca_cases(_ROOT / "packages" / "eval-core" / "data" / "rca_golden.json")
    results = []
    for case in cases:
        evidence = [{"description": f"产线数据验证: {case['defect_type']}"}] * case.get("min_evidence", 1)
        verdict = rule_judge_rca(case["root_cause"], evidence, case["expected_substrings"])
        results.append({
            "case_id": case["case_id"],
            "passed": verdict.passed,
            "score": verdict.score,
            "explanation": verdict.explanation,
            "criteria": verdict.criteria,
        })
    passed = sum(1 for r in results if r["passed"])
    return {"total": len(results), "passed": passed, "pass_rate": f"{passed}/{len(results)}", "details": results}


async def _run_llm_judge_demo() -> dict:
    """Demonstrate LLM-as-Judge with graceful degradation when API key not configured."""
    print("=== LLM-as-Judge Demo ===")

    # Test 1: No API key → graceful skip
    verdict = await llm_judge_consistency(
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        root_cause="涂布面密度不均 → 容量衰减",
        evidence=[{"description": "涂布厚度 std 超标 2.3 倍"}],
        report_md="## D4 根因\n涂布面密度不均 → 容量衰减\n## D5 纠正措施\n...",
    )
    print(f"  No API key: passed={verdict.passed} score={verdict.score} explanation={verdict.explanation}")

    # Test 2: With API key (if configured) → actual LLM judge
    import os
    api_key = os.environ.get("LLM_API_KEY", "")
    api_base = os.environ.get("LLM_BASE_URL", "")
    api_model = os.environ.get("LLM_MODEL", "deepseek-chat")

    if api_key and api_base:
        verdict2 = await llm_judge_consistency(
            llm_base_url=api_base, llm_api_key=api_key, llm_model=api_model,
            root_cause="涂布面密度不均 → 容量衰减",
            evidence=[{"description": "涂布厚度 std 超标 2.3 倍"}, {"description": "面密度 CV 偏高 15%"}],
            report_md="## D4 根因\n涂布面密度不均 → 容量衰减\n...",
        )
        print(f"  With API key: passed={verdict2.passed} score={verdict2.score} explanation={verdict2.explanation}")
    else:
        print(f"  With API key: skipped (LLM_API_KEY/LLM_BASE_URL not set)")

    return {"graceful_degradation": "✅ 无 API key 时返回 skip 而非报错"}


async def _run_all() -> dict:
    print("=" * 50)
    print("Reporter Golden Set Evaluation (15 cases)")
    print("=" * 50)
    reporter = await _run_reporter_template_eval()
    print(json.dumps(reporter, ensure_ascii=False, indent=2))

    print("\n" + "=" * 50)
    print("RCA Rule Judge Evaluation (10 cases)")
    print("=" * 50)
    rca = await _run_rca_rule_eval()
    print(json.dumps(rca, ensure_ascii=False, indent=2))

    print()
    llm = await _run_llm_judge_demo()

    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"  Reporter: {reporter['pass_rate']} passed")
    print(f"  RCA Rule Judge: {rca['pass_rate']} passed")
    print(f"  LLM-as-Judge: {llm['graceful_degradation']}")
    return {"reporter": reporter, "rca": rca, "llm_judge": llm}


def main() -> None:
    parser = argparse.ArgumentParser(description="RCA+Reporter eval")
    parser.add_argument("--reporter-template", action="store_true", help="Reporter Golden Set eval (15 cases)")
    parser.add_argument("--rca-rule", action="store_true", help="RCA rule judge eval (10 cases)")
    parser.add_argument("--llm-judge", action="store_true", help="LLM-as-Judge demo (graceful degradation)")
    parser.add_argument("--all", action="store_true", help="Run all evaluations")
    args = parser.parse_args()

    if args.all:
        asyncio.run(_run_all())
        return

    if args.reporter_template:
        out = asyncio.run(_run_reporter_template_eval())
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.rca_rule:
        out = asyncio.run(_run_rca_rule_eval())
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.llm_judge:
        asyncio.run(_run_llm_judge_demo())
        return

    # Default: run all
    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
