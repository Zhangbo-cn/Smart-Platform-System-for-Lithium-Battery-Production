"""LLM-as-Judge 与离线评测。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class JudgeVerdict:
    passed: bool
    score: float
    explanation: str
    criteria: dict[str, bool]


def rule_judge_rca(root_cause: str, evidence: list[dict[str, Any]], expected_substrings: list[str]) -> JudgeVerdict:
    """规则 Judge：根因是否被证据支撑（无需 LLM）。"""
    if not root_cause:
        return JudgeVerdict(False, 0.0, "empty root_cause", {"has_root_cause": False})
    hit = any(exp.lower() in root_cause.lower() for exp in expected_substrings)
    ev_ok = len(evidence) >= 1
    passed = hit and ev_ok
    score = 1.0 if passed else 0.4 if root_cause else 0.0
    return JudgeVerdict(
        passed=passed,
        score=score,
        explanation="rule: root_cause matches expected and evidence present",
        criteria={"expected_hit": hit, "evidence_present": ev_ok},
    )


def rule_judge_reporter_d4_locked(report_md: str, locked_root_cause: str) -> JudgeVerdict:
    """Reporter Judge：D4 根因字面未被改写。"""
    if not locked_root_cause:
        return JudgeVerdict(False, 0.0, "no locked root cause", {"d4_locked": False})
    locked = locked_root_cause.strip()
    passed = locked in report_md
    return JudgeVerdict(
        passed=passed,
        score=1.0 if passed else 0.0,
        explanation="D4 contains exact locked root_cause text",
        criteria={"d4_locked": passed},
    )


async def llm_judge_consistency(
    *,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    root_cause: str,
    evidence: list[dict[str, Any]],
    report_md: str,
) -> JudgeVerdict:
    """LLM-as-Judge：证据与根因一致性（离线评测用）。"""
    if not llm_base_url or not llm_api_key:
        return JudgeVerdict(
            True,
            0.5,
            "llm judge skipped (no api key)",
            {"skipped": True},
        )

    import httpx

    prompt = (
        "你是质量审计 Judge。只评「根因结论是否有证据支撑」「报告是否胡编工序」。\n"
        f"根因：{root_cause}\n"
        f"证据：{json.dumps(evidence[:8], ensure_ascii=False)}\n"
        f"报告摘要：{report_md[:2000]}\n"
        '输出 JSON：{"passed": true/false, "score": 0-1, "explanation": "..."}'
    )
    url = f"{llm_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {llm_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": llm_model,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": prompt}],
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
    try:
        data = json.loads(text)
        return JudgeVerdict(
            passed=bool(data.get("passed")),
            score=float(data.get("score", 0.0)),
            explanation=str(data.get("explanation", "")),
            criteria={"llm_judge": True},
        )
    except json.JSONDecodeError:
        return JudgeVerdict(False, 0.0, f"invalid judge output: {text[:200]}", {"parse_error": True})
