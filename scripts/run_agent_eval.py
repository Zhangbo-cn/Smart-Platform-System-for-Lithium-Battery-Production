"""四 Agent 统一评测 CLI：Planner / Orchestrator / RCA(规则) / Reporter。

用法:
  python scripts/run_agent_eval.py --all          # 全量评测（默认）
  python scripts/run_agent_eval.py --rca           # 仅 RCA 规则评测（17例）
  python scripts/run_agent_eval.py --reporter      # 仅 Reporter 规则评测（15例）
  python scripts/run_agent_eval.py --planner       # 仅 Planner 规则评测（6例）
  python scripts/run_agent_eval.py --orchestrator  # 仅 Orchestrator 评测（5例）
  python scripts/run_agent_eval.py --llm-judge     # LLM-as-Judge + 规则评测
"""

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

from eval_core.judge import JudgeVerdict, rule_judge_rca, rule_judge_reporter_d4_locked
from eval_core.reporter_eval import ReporterGoldenCase, evaluate_report, load_reporter_cases
from eval_core.scorers import RuleScorer, llm_judge_agent
from platform_contracts.agent_handoffs import Report8dRequest
from report_runner import _build_report_md


# ── 数据加载 ──────────────────────────────────────────────

DATA_DIR = _ROOT / "packages" / "eval-core" / "data"


def _load_cases(filename: str) -> list[dict]:
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


# ── 各 Agent 评测 ──────────────────────────────────────────


def _eval_planner_rule() -> dict:
    """Planner 规则评测：playbook 选择 + 参数提取。"""
    cases = _load_cases("planner_golden.json")
    results = []
    for case in cases:
        # Planner 输出的 mock（真实场景调 A2A）
        mock_output = {
            "playbook": case.get("expected_playbook", "investigate"),
            "params": {k: v for k, v in case.get("expected_params", {}).items() if k != "defect_type"},
        }
        score = RuleScorer.score_planner(mock_output, case)
        results.append({
            "case_id": case["case_id"],
            "passed": score.passed,
            "score": round(score.total_score, 1),
            "details": [{"check": d.check_name, "passed": d.passed, "detail": d.detail}
                        for d in score.rule_details],
        })
    passed = sum(1 for r in results if r["passed"])
    return {"agent": "planner", "total": len(results), "passed": passed, "pass_rate": f"{passed}/{len(results)}", "details": results}


def _eval_orchestrator_rule() -> dict:
    """Orchestrator 规则评测：步骤完整性 + 上下文传递。"""
    cases = _load_cases("orchestrator_golden.json")
    results = []
    for case in cases:
        ctx = {
            "current_step": case["expected_steps"][-1] if case["expected_steps"] else "",
            "defect_type": case["input"].get("defect_type", ""),
            "rca": {"root_cause": "mock原因" if "rca" in case["expected_steps"] else ""},
            "report_8d": {"report_md": "mock报告" if "report_8d" in case["expected_steps"] else ""},
        }
        score = RuleScorer.score_orchestrator(ctx, case)
        results.append({
            "case_id": case["case_id"],
            "passed": score.passed,
            "score": round(score.total_score, 1),
            "details": [{"check": d.check_name, "passed": d.passed, "detail": d.detail}
                        for d in score.rule_details],
        })
    passed = sum(1 for r in results if r["passed"])
    return {"agent": "orchestrator", "total": len(results), "passed": passed, "pass_rate": f"{passed}/{len(results)}", "details": results}


def _eval_rca_rule() -> dict:
    """RCA 规则评测（17 case，含 2 HITL）。"""
    cases = _load_cases("rca_golden.json")
    results = []
    for case in cases:
        # Mock RCA output（真实场景调 A2A）
        is_hitl = case.get("requires_hitl", False)
        mock_output = {
            "root_cause": "" if is_hitl else case["root_cause"],
            "confidence": 0.0 if is_hitl else 0.85,
            "evidence": [{"description": f"产线数据验证: {case['defect_type']}"}] * case.get("min_evidence", 1),
            "requires_hitl": is_hitl,
            "thread_id": f"thread_{case['case_id']}" if is_hitl else None,
        }
        score = RuleScorer.score_rca(mock_output, case)
        results.append({
            "case_id": case["case_id"],
            "passed": score.passed,
            "score": round(score.total_score, 1),
            "details": [{"check": d.check_name, "passed": d.passed, "detail": d.detail}
                        for d in score.rule_details],
        })
    passed = sum(1 for r in results if r["passed"])
    return {"agent": "quality-rca-agent", "total": len(results), "passed": passed, "pass_rate": f"{passed}/{len(results)}", "details": results}


async def _eval_reporter_rule() -> dict:
    """Reporter 规则评测（15 case）。"""
    cases = load_reporter_cases(str(DATA_DIR / "reporter_golden.json"))
    results = []
    for case in cases:
        req = Report8dRequest(
            session_id=f"eval-{case.case_id}",
            root_cause=case.root_cause,
            hitl_approved=True,
            defect_type=case.defect_type,
            confidence=0.85,
        )
        md, recs = _build_report_md(req)
        report_output = {
            "report_md": md,
            "capa_id": f"CAPA-{case.case_id}",
            "generation_mode": "template",
            "recommendations": list(recs),
        }
        score = RuleScorer.score_reporter(report_output, case)
        results.append({
            "case_id": case.case_id,
            "passed": score.passed,
            "score": round(score.total_score, 1),
            "details": [{"check": d.check_name, "passed": d.passed, "detail": d.detail}
                        for d in score.rule_details],
        })
    passed = sum(1 for r in results if r["passed"])
    return {"agent": "report-reporter-agent", "total": len(results), "passed": passed, "pass_rate": f"{passed}/{len(results)}", "details": results}


async def _eval_llm_judge() -> dict:
    """LLM-as-Judge 演示。"""
    import os
    api_key = os.environ.get("LLM_API_KEY", "")
    api_base = os.environ.get("LLM_BASE_URL", "")
    api_model = os.environ.get("LLM_MODEL", "deepseek-chat")

    if not api_key or not api_base:
        return {"note": "LLM-as-Judge skipped (LLM_API_KEY/LLM_BASE_URL not set). Set them to enable Rubric scoring."}

    results = {}

    # RCA Judge
    rca_result = await llm_judge_agent(
        "quality-rca-agent",
        {"root_cause": "刮刀磨损→面密度漂移→容量偏差", "confidence": 0.85, "evidence": [{"description": "涂布厚度std超标2.3倍"}]},
        llm_base_url=api_base, llm_api_key=api_key, llm_model=api_model,
    )
    results["rca_judge"] = rca_result

    # Reporter Judge
    rep_result = await llm_judge_agent(
        "report-reporter-agent",
        {"report_md": "## 8D 质量报告\n## D4 根因\n刮刀磨损→面密度漂移\n## D5 纠正措施\n1. 更换刮刀\n2. 缩短保养周期"},
        llm_base_url=api_base, llm_api_key=api_key, llm_model=api_model,
    )
    results["reporter_judge"] = rep_result

    return results


async def _run_all() -> dict:
    print("=" * 60)
    print("Planner 规则评测 (6 cases)")
    print("=" * 60)
    planner = _eval_planner_rule()
    _print_results(planner)

    print("\n" + "=" * 60)
    print("Orchestrator 规则评测 (5 cases)")
    print("=" * 60)
    orch = _eval_orchestrator_rule()
    _print_results(orch)

    print("\n" + "=" * 60)
    print("RCA 规则评测 (17 cases)")
    print("=" * 60)
    rca = _eval_rca_rule()
    _print_results(rca)

    print("\n" + "=" * 60)
    print("Reporter 规则评测 (15 cases)")
    print("=" * 60)
    reporter = await _eval_reporter_rule()
    _print_results(reporter)

    print("\n" + "=" * 60)
    print("LLM-as-Judge")
    print("=" * 60)
    llm = await _eval_llm_judge()
    if "note" in llm:
        print(f"  {llm['note']}")
    else:
        print(f"  RCA Judge:   {json.dumps(llm.get('rca_judge', {}), ensure_ascii=False)}")
        print(f"  Reporter Judge: {json.dumps(llm.get('reporter_judge', {}), ensure_ascii=False)}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_results = {"planner": planner, "orchestrator": orch, "rca": rca, "reporter": reporter}
    for name, res in all_results.items():
        print(f"  {name:12s} {res['pass_rate']:>8s} passed  (avg score: {_avg_score(res):.1f})")

    return all_results | {"llm_judge": llm}


def _print_results(res: dict) -> None:
    for d in res.get("details", []):
        status = "✅" if d["passed"] else "❌"
        print(f"  {status} {d['case_id']}: {d['score']:.0f}分")
    print(f"\n  通过率: {res['pass_rate']}  ({res['passed']}/{res['total']})")


def _avg_score(res: dict) -> float:
    scores = [d["score"] for d in res.get("details", [])]
    return sum(scores) / len(scores) if scores else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Battery Agent 统一评测")
    parser.add_argument("--all", action="store_true", help="全量评测")
    parser.add_argument("--planner", action="store_true")
    parser.add_argument("--orchestrator", action="store_true")
    parser.add_argument("--rca", action="store_true")
    parser.add_argument("--reporter", action="store_true")
    parser.add_argument("--llm-judge", action="store_true")
    args = parser.parse_args()

    if args.planner:
        print(json.dumps(_eval_planner_rule(), ensure_ascii=False, indent=2))
        return
    if args.orchestrator:
        print(json.dumps(_eval_orchestrator_rule(), ensure_ascii=False, indent=2))
        return
    if args.rca:
        print(json.dumps(_eval_rca_rule(), ensure_ascii=False, indent=2))
        return
    if args.reporter:
        print(json.dumps(asyncio.run(_eval_reporter_rule()), ensure_ascii=False, indent=2))
        return
    if args.llm_judge:
        print(json.dumps(asyncio.run(_eval_llm_judge()), ensure_ascii=False, indent=2))
        return

    # Default: all
    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
