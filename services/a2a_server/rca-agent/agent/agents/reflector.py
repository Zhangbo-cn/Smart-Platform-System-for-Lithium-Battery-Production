from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from agent.agents.base import BaseAgent
from agent.llm import DataSensitivity, TaskDifficulty
from agent.state import EvidenceItem, QualityAnalysisState
from harness.validation import FMEAValidator
from knowledge.fmea_registry import get_tree

logger = structlog.get_logger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "reflector_system.md"


class ReflectorAgent(BaseAgent):
    """
    双路反思验证：
      规则引擎（FMEAValidator）为主——确定性、0 延迟、可审计：
        · 判断哪些因果链路命中
        · 用加权公式算置信度（不是 LLM 拍数字）
        · 决定补查策略：单因深挖 / 多因横向关联 / 重规划 / 降级
      LLM 为辅——只在规则覆盖不到时补充语义判断：
        · FMEA 树没命中（疑似新缺陷模式）时做开放推理
        · 多因耦合时辅助生成关联假设

    动态补查预算：max_reflection_loops 由命中链路的剩余深度算出，
    而不是写死的常数——单因看深度，多因看关联宽度。
    """

    name = "reflector"

    async def run(self, state: QualityAnalysisState) -> dict[str, Any]:
        loop = state.get("reflection_loops", 0) + 1
        tool_calls = state.get("tool_calls", [])
        defect_type = state.get("defect_type") or self._infer_defect_type(state)

        tree = get_tree(defect_type)
        if tree is None:
            # 没有对应 FMEA 树 —— 知识盲区，退回纯 LLM 判断（慢路径兜底）
            return await self._llm_only_fallback(state, loop, reason=f"无 '{defect_type}' 的 FMEA 因果树")

        validator = FMEAValidator(tree, hitl_threshold=self.settings.hitl_confidence_threshold)

        # ---- 规则引擎：命中判断 + 置信度（确定性，不调 LLM）----
        hits = validator.evaluate_branches(tool_calls)
        coverage = validator.compute_coverage(tool_calls)
        rule_confidence = validator.compute_confidence(hits, coverage)
        decision = validator.decide_strategy(hits, loop)

        logger.info(
            "reflector.rule_eval",
            loop=loop, defect=defect_type, n_hits=len(hits),
            coverage=coverage, rule_confidence=rule_confidence, mode=decision.mode,
            budget=decision.budget,
        )

        # ---- 动态预算：补查上限来自策略，不是写死的 3 ----
        max_loops = max(decision.budget, loop)  # 保证至少能完成本轮

        # ===== 路由分发 =====
        # CONFIRM：单链挖到根因层且置信度达标 → 出报告（需 FTA 顶事件闭合）
        if decision.mode == "CONFIRM" and rule_confidence >= self.settings.hitl_confidence_threshold:
            fta_closed, fta_note = validator.validate_fta_closure(hits)
            report = self._to_report(hits, rule_confidence, loop, decision)
            report["fta_closed"] = fta_closed
            report["fta_note"] = fta_note
            if validator.requires_hitl_for_fta(hits, rule_confidence):
                report["requires_hitl"] = True
                report["status"] = "hitl"
            return report

        # DEEPEN / CORRELATE / REPLAN：还有预算 → 回 Executor 补查
        if decision.mode in ("DEEPEN", "CORRELATE", "REPLAN") and loop < max_loops:
            return await self._to_refine(state, decision, hits, rule_confidence, loop, max_loops, defect_type)

        # 预算耗尽 / DEGRADE / 置信度不足 → 优雅降级，转人工
        return self._to_degrade(tree, hits, rule_confidence, loop, decision)

    # ------------------------------------------------------------------
    # 路由目标 1：出报告
    # ------------------------------------------------------------------
    def _to_report(self, hits, confidence, loop, decision) -> dict[str, Any]:
        root = hits[0]
        evidence = self._hits_to_evidence(hits)
        # 根因结论与改进建议来自确定性来源（FMEA 命中节点），不交给 LLM 重新判断。
        # Reporter 只负责把它们组织成 8D 报告文字。
        node = root.current_node
        recommendations = []
        if node.recommendation:
            if node.recommendation.get("immediate"):
                recommendations.append(f"立即：{node.recommendation['immediate']}")
            if node.recommendation.get("long_term"):
                recommendations.append(f"长期：{node.recommendation['long_term']}")
        return {
            "reflection_loops": loop,
            "need_more_data": False,
            "confidence": confidence,
            "evidence": evidence,
            "root_cause": f"{root.root.name} → {node.name}",
            "recommendations": recommendations,
            "refine_mode": decision.mode,
            "requires_hitl": False,
            "status": "reporting",
        }

    # ------------------------------------------------------------------
    # 路由目标 2：补查（含多因耦合时的 LLM 辅助 + 难案子升级模型）
    # ------------------------------------------------------------------
    async def _to_refine(self, state, decision, hits, confidence, loop, max_loops, defect_type) -> dict[str, Any]:
        queries = list(decision.queries)

        # 多因耦合且已到第 2 轮：调 LLM 做关联推理 + 升级模型（增强重试）
        if decision.mode == "CORRELATE" and loop >= 2:
            self.settings.llm_primary_model  # noqa  (实际部署可在此切换到更强模型)
            llm_hypotheses = await self._llm_correlation(state, hits)
            queries.extend(llm_hypotheses)

        # REPLAN：FMEA 树首轮没命中，让 LLM 重新给取证方向
        if decision.mode == "REPLAN":
            queries = await self._llm_replan(state)

        return {
            "reflection_loops": loop,
            "max_reflection_loops": max_loops,
            "need_more_data": True,
            "additional_queries": queries,
            "confidence": confidence,
            "evidence": self._hits_to_evidence(hits),
            "refine_mode": decision.mode,
            "requires_hitl": False,
            "status": "executing",
        }

    # ------------------------------------------------------------------
    # 路由目标 3：优雅降级（不硬猜，给"已排除/疑似"清单，转人工）
    # ------------------------------------------------------------------
    def _to_degrade(self, tree, hits, confidence, loop, decision) -> dict[str, Any]:
        hit_roots = {h.root.name for h in hits}
        excluded = [b.name for b in tree.root_branches if b.name not in hit_roots]
        suspected = [h.root.name for h in hits]
        return {
            "reflection_loops": loop,
            "need_more_data": False,
            "confidence": confidence,
            "evidence": self._hits_to_evidence(hits),
            "refine_mode": decision.mode,
            "requires_hitl": True,
            "partial_result": {
                "excluded": excluded,        # 数据正常、已排除的链路
                "suspected": suspected,      # 疑似但未坐实的链路
                "reason": decision.reason or "挖到因果树底仍无法确定单一根因",
            },
            "status": "hitl",
        }

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _hits_to_evidence(hits) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        for h in hits:
            evidence.append(EvidenceItem(
                description=f"{h.root.name} → {h.current_node.name}：指标偏离 {h.severity:.2f} 倍阈值"
                            + (f"（实测 {h.metric_value}）" if h.metric_value is not None else ""),
                source_tool=h.current_node.tool or "",
                data_ref=h.current_node.metric_key or "",
                confidence=round(min(h.severity / 3.0, 1.0), 3),
            ))
        return evidence

    @staticmethod
    def _infer_defect_type(state: QualityAnalysisState) -> str:
        """从用户问题里粗略识别缺陷类型（生产环境可换成 BERT 分类器）。"""
        q = state.get("user_query", "")
        for key in ("容量衰减", "容量低", "内阻高", "析锂", "漏液", "短路", "外观"):
            if key in q:
                return "容量衰减" if key in ("容量衰减", "容量低") else key
        return "容量衰减"

    async def _llm_correlation(self, state, hits) -> list[dict[str, Any]]:
        """多因耦合：让 LLM 提出关联假设 + 给出可验证路径。"""
        hit_desc = "、".join(f"{h.root.name}({h.current_node.name})" for h in hits)
        prompt = (
            f"以下因果链路同时异常：{hit_desc}。\n"
            "请判断它们是否可能耦合致命，并给出可用数据验证的查询。\n"
            '严格输出 JSON：{"queries": [{"tool": "...", "tool_args": {}, "action": "...", '
            '"hypothesis": "耦合假设", "verification": "如何用数据证伪"}]}'
        )
        try:
            resp = await self.call_llm(
                system="你是锂电失效分析专家，只输出可被数据证伪的耦合假设。",
                messages=[{"role": "user", "content": prompt}],
                difficulty=TaskDifficulty.COMPLEX,
                sensitivity=DataSensitivity.MEDIUM,
                caller="reflector._llm_correlation",
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return json.loads(text).get("queries", [])
        except (json.JSONDecodeError, Exception):  # noqa
            return []

    async def _llm_replan(self, state) -> list[dict[str, Any]]:
        """FMEA 树未命中：让 LLM 开放推理新的取证方向。"""
        prompt = (
            f"用户问题：{state['user_query']}\n"
            "已有的标准排查方向均未发现异常。请提出 FMEA 树未覆盖的新排查方向，"
            '并给出可验证查询。严格输出 JSON：{"queries": [{"tool":"...","tool_args":{},"action":"..."}]}'
        )
        try:
            resp = await self.call_llm(
                system=PROMPT_PATH.read_text(encoding="utf-8"),
                messages=[{"role": "user", "content": prompt}],
                difficulty=TaskDifficulty.COMPLEX,
                sensitivity=DataSensitivity.MEDIUM,
                caller="reflector._llm_replan",
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return json.loads(text).get("queries", [])
        except (json.JSONDecodeError, Exception):  # noqa
            return []

    async def _llm_only_fallback(self, state, loop, reason) -> dict[str, Any]:
        """没有 FMEA 树时的纯 LLM 兜底（保留旧行为，但置信度强制压低）。"""
        logger.warning("reflector.no_fmea_tree", reason=reason)
        text_prompt = (
            f"用户问题：{state['user_query']}\n\n"
            f"工具调用结果：{json.dumps(state.get('tool_calls', []), ensure_ascii=False, default=str)[:8000]}\n\n"
            '严格输出 JSON：{"need_more_data": bool, "additional_queries": [], "confidence": float, "evidence": []}'
        )
        try:
            resp = await self.call_llm(
                system=PROMPT_PATH.read_text(encoding="utf-8"),
                messages=[{"role": "user", "content": text_prompt}],
                difficulty=TaskDifficulty.COMPLEX,
                sensitivity=DataSensitivity.MEDIUM,
                caller="reflector._llm_only_fallback",
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            parsed = json.loads(text)
        except (json.JSONDecodeError, Exception):  # noqa
            parsed = {"need_more_data": False, "additional_queries": [], "confidence": 0.4, "evidence": []}

        # 新因果/无树场景：置信度上限压到 0.75，强制人工复核
        confidence = min(float(parsed.get("confidence", 0.4)), 0.75)
        need_more = bool(parsed.get("need_more_data", False)) and loop < self.settings.max_reflection_loops
        return {
            "reflection_loops": loop,
            "need_more_data": need_more,
            "additional_queries": parsed.get("additional_queries", []),
            "confidence": confidence,
            "evidence": [EvidenceItem(**e) for e in parsed.get("evidence", []) if isinstance(e, dict)],
            "requires_hitl": confidence < self.settings.hitl_confidence_threshold and not need_more,
            "refine_mode": "LLM_FALLBACK",
            "status": "hitl" if (confidence < self.settings.hitl_confidence_threshold and not need_more)
                      else ("executing" if need_more else "reporting"),
        }
