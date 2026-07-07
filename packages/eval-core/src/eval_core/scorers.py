"""Scorers：Rule（确定性）+ LLM-as-Judge + 聚合评分。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from eval_core.judge import JudgeVerdict


# ── 评分结果类型 ──────────────────────────────────────────────


@dataclass
class ScoreDetail:
    """单条检查项的评分结果。"""
    check_name: str
    passed: bool
    score: float
    max_score: float
    detail: str = ""


@dataclass
class AgentScore:
    """一个 Agent 在一条用例上的完整评分。"""
    case_id: str
    agent: str
    rule_score: float = 0.0
    rule_max: float = 0.0
    rule_details: list[ScoreDetail] = field(default_factory=list)
    llm_score: float = 0.0
    llm_max: float = 0.0
    llm_details: list[ScoreDetail] = field(default_factory=list)
    efficiency_penalty: float = 0.0
    total_score: float = 0.0
    passed: bool = False
    trace: dict[str, Any] = field(default_factory=dict)

    def add_rule_check(self, name: str, passed: bool, weight: float = 1.0, detail: str = ""):
        self.rule_details.append(ScoreDetail(
            check_name=name, passed=passed,
            score=weight if passed else 0.0,
            max_score=weight, detail=detail,
        ))
        self.rule_score = sum(d.score for d in self.rule_details)
        self.rule_max = sum(d.max_score for d in self.rule_details)

    def add_llm_check(self, name: str, score: float, max_score: float, detail: str = ""):
        self.llm_details.append(ScoreDetail(
            check_name=name, passed=score >= max_score * 0.6,
            score=score, max_score=max_score, detail=detail,
        ))
        self.llm_score = sum(d.score for d in self.llm_details)
        self.llm_max = sum(d.max_score for d in self.llm_details)

    def finalize(self, efficiency_penalty: float = 0.0, pass_threshold: float = 80.0):
        self.efficiency_penalty = efficiency_penalty
        rule_pct = (self.rule_score / self.rule_max * 60) if self.rule_max > 0 else 0
        llm_pct = (self.llm_score / self.llm_max * 30) if self.llm_max > 0 else 0
        eff_pct = max(0, 10 - efficiency_penalty)
        self.total_score = rule_pct + llm_pct + eff_pct
        self.passed = self.total_score >= pass_threshold


# ── Rule Scorer ────────────────────────────────────────────────


class RuleScorer:
    """确定性评分器：每个 Agent 的规则检查项。"""

    @staticmethod
    def score_planner(output: dict[str, Any], expected: dict[str, Any]) -> AgentScore:
        score = AgentScore(case_id=expected.get("case_id", "?"), agent="planner")
        trace = {"output": output}

        playbook_ok = output.get("playbook") in ("investigate", "trace_only", "rca", "close_loop")
        score.add_rule_check("playbook_valid", playbook_ok, weight=5,
                             detail=f"playbook={output.get('playbook')}")

        params = output.get("params", {})
        has_msg = bool(params.get("message") or output.get("message"))
        score.add_rule_check("has_message", has_msg, weight=3,
                             detail=f"message={'✓' if has_msg else '✗'}")

        score.finalize()
        score.trace = trace
        return score

    @staticmethod
    def score_orchestrator(
        ctx: dict[str, Any],
        expected: dict[str, Any],
        trace: dict[str, Any] | None = None,
    ) -> AgentScore:
        score = AgentScore(case_id=expected.get("case_id", "?"), agent="orchestrator")
        trace_data = trace or {}

        # 步骤完整性
        expected_steps = expected.get("steps", ["triage", "trace", "rca", "report_8d"])
        actual_steps = ctx.get("current_step", "")
        has_steps = bool(actual_steps)
        score.add_rule_check("steps_completed", has_steps, weight=10,
                             detail=f"steps={actual_steps}")

        # PlatformContext 字段完整性
        if expected.get("defect_type"):
            dt_ok = ctx.get("defect_type") == expected["defect_type"]
            score.add_rule_check("defect_type_match", dt_ok, weight=5,
                                 detail=f"defect_type={ctx.get('defect_type')}")

        # 关键产出存在
        if "root_cause" in str(expected):
            rc_ok = bool(ctx.get("rca", {}).get("root_cause"))
            score.add_rule_check("rca_produced", rc_ok, weight=5)

        score.finalize()
        score.trace = trace_data
        return score

    @staticmethod
    def score_rca(output: dict[str, Any], expected: dict[str, Any]) -> AgentScore:
        score = AgentScore(case_id=expected.get("case_id", "?"), agent="quality-rca-agent")
        trace = {"output": output}

        # 根因非空
        rc = output.get("root_cause", "")
        rc_ok = bool(rc)
        score.add_rule_check("root_cause_not_empty", rc_ok, weight=10,
                             detail=f"root_cause={'✓' if rc else '✗'}")

        # 置信度 > 0
        conf = output.get("confidence", 0)
        conf_ok = conf > 0
        score.add_rule_check("confidence_positive", conf_ok, weight=5,
                             detail=f"confidence={conf}")

        # 预期关键词命中
        for kw in expected.get("root_cause_contains", []):
            hit = kw in rc
            score.add_rule_check(f"keyword:{kw}", hit, weight=3,
                                 detail=f"{'✓' if hit else '✗'} found in root_cause")

        # evidence 数量
        evidence = output.get("evidence", []) or []
        min_ev = expected.get("min_evidence", 0)
        ev_ok = len(evidence) >= min_ev
        score.add_rule_check(f"evidence_min_{min_ev}", ev_ok, weight=5,
                             detail=f"evidence_count={len(evidence)}")

        # HITL 处理
        if expected.get("requires_hitl") is not None:
            hitl_ok = output.get("requires_hitl") == expected["requires_hitl"]
            score.add_rule_check("hitl_correct", hitl_ok, weight=5,
                                 detail=f"requires_hitl={output.get('requires_hitl')}")
            if expected["requires_hitl"]:
                tid_ok = bool(output.get("thread_id"))
                score.add_rule_check("hitl_has_thread_id", tid_ok, weight=3)

        score.finalize()
        score.trace = trace
        return score

    @staticmethod
    def score_reporter(output: dict[str, Any], expected: dict[str, Any]) -> AgentScore:
        score = AgentScore(case_id=expected.get("case_id", "?"), agent="report-reporter-agent")
        trace = {"output": output}

        report = output.get("report_md", "")

        # D4 根因锁定
        locked = expected.get("root_cause", "")
        if locked:
            d4_ok = locked in report
            score.add_rule_check("d4_root_cause_locked", d4_ok, weight=10,
                                 detail=f"D4 locked={'✓' if d4_ok else '✗'}")

        # 报告长度
        min_len = expected.get("min_report_chars", 200)
        len_ok = len(report) >= min_len
        score.add_rule_check(f"report_min_{min_len}chars", len_ok, weight=5,
                             detail=f"report_len={len(report)}")

        # CAPA ID 非空
        capa_ok = bool(output.get("capa_id"))
        score.add_rule_check("capa_id_created", capa_ok, weight=5,
                             detail=f"capa_id={'✓' if capa_ok else '✗'}")

        score.finalize()
        score.trace = trace
        return score


# ── LLM-as-Judge ──────────────────────────────────────────────


_LLM_JUDGE_PROMPTS: dict[str, str] = {
    "quality-rca-agent": """你是一个锂电质量分析评判专家。请评估以下 RCA 分析的质量。

评分维度（每项 0-5 分）：
1. reasoning_coherence：推理链路是否完整、自洽，没有跳步
2. evidence_support：根因是否有充分的 evidence 支撑
3. terminology_correctness：专业术语（涂布/辊压/析锂/面密度等）使用是否正确

根因：{root_cause}
置信度：{confidence}
证据：{evidence_summary}

输出 JSON 格式（不要 markdown 包裹）：
{{"reasoning_coherence": 0-5, "evidence_support": 0-5, "terminology_correctness": 0-5, "explanation": "..."}}
""",
    "report-reporter-agent": """你是一个 8D 质量报告评审专家。请评估以下 8D 报告质量。

评分维度（每项 0-5 分）：
1. d5_actionability：D5 纠正措施是否具体、可执行
2. report_completeness：报告结构（D1-D8）是否完整
3. clarity_professionalism：表述是否清晰、专业

报告摘要：{report_summary}

输出 JSON 格式：
{{"d5_actionability": 0-5, "report_completeness": 0-5, "clarity_professionalism": 0-5, "explanation": "..."}}
""",
    "planner-agent": """你是一个任务规划评审专家。请评估以下 Planner 输出的质量。

评分维度（每项 0-5 分）：
1. playbook_appropriateness：选定的 playbook 是否适合用户描述的问题
2. param_accuracy：提取的参数（batch_id, defect_type 等）是否准确

用户描述：{user_query}
Planner 输出：{planner_output}

输出 JSON 格式：
{{"playbook_appropriateness": 0-5, "param_accuracy": 0-5, "explanation": "..."}}
""",
}


async def llm_judge_agent(
    agent_name: str,
    context: dict[str, Any],
    *,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "deepseek-chat",
) -> dict[str, Any]:
    """LLM-as-Judge 评分器。

    返回: {dimension: score, "explanation": str} 或
          {error: str, "skipped": True} (API key 未配置时)
    """
    if not llm_base_url or not llm_api_key:
        return {d: 2.5 for d in _dimensions_for(agent_name)} | {"explanation": "skipped_no_api_key", "skipped": True}

    prompt_template = _LLM_JUDGE_PROMPTS.get(agent_name)
    if not prompt_template:
        return {"error": f"no judge prompt for {agent_name}", "skipped": True}

    prompt = _format_judge_prompt(prompt_template, context)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{llm_base_url.rstrip('/')}/chat/completions",
                json={
                    "model": llm_model,
                    "temperature": 0.0,
                    "messages": [{"role": "user", "content": prompt}],
                },
                headers={"Authorization": f"Bearer {llm_api_key}", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            result = json.loads(text)
            return {k: v for k, v in result.items() if k != "explanation"} | {
                "explanation": result.get("explanation", ""),
                "skipped": False,
            }
    except Exception as exc:
        return {d: 0.0 for d in _dimensions_for(agent_name)} | {
            "explanation": f"llm_judge_error: {exc}",
            "skipped": True,
        }


def _dimensions_for(agent_name: str) -> list[str]:
    mapping = {
        "quality-rca-agent": ["reasoning_coherence", "evidence_support", "terminology_correctness"],
        "report-reporter-agent": ["d5_actionability", "report_completeness", "clarity_professionalism"],
        "planner-agent": ["playbook_appropriateness", "param_accuracy"],
    }
    return mapping.get(agent_name, [])


def _format_judge_prompt(template: str, ctx: dict[str, Any]) -> str:
    root_cause = ctx.get("root_cause", ctx.get("output", {}).get("root_cause", "N/A"))
    confidence = ctx.get("confidence", ctx.get("output", {}).get("confidence", 0))
    evidence = ctx.get("evidence", ctx.get("output", {}).get("evidence", []))
    report = ctx.get("report_md", ctx.get("output", {}).get("report_md", ""))
    planner_output = ctx.get("planner_output", json.dumps(ctx.get("output", {}), ensure_ascii=False))

    return template.format(
        root_cause=str(root_cause)[:500],
        confidence=confidence,
        evidence_summary=json.dumps(
            [{"description": e.get("description", "")[:100]} for e in (evidence or [])[:5]],
            ensure_ascii=False,
        ),
        report_summary=str(report)[:1000],
        user_query=str(ctx.get("user_query", ctx.get("message", "")))[:300],
        planner_output=str(planner_output)[:500],
    )
