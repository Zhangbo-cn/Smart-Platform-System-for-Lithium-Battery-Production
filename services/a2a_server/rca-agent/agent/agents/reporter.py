from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.agents.base import BaseAgent
from agent.llm import DataSensitivity, TaskDifficulty
from agent.state import QualityAnalysisState

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "reporter_system.md"


class ReporterAgent(BaseAgent):
    """
  RCA 内 Reporter 节点：产出结构化 rca_artifacts 草稿，不做完整 8D 定稿。

  完整 8D 由平台 report-reporter-agent（Deep Agents）在 HITL 后生成。
  根因与建议来自 Reflector 规则引擎，本节点仅做短摘要表达。
    """

    name = "reporter"

    async def run(self, state: QualityAnalysisState) -> dict[str, Any]:
        root_cause = state.get("root_cause", "")
        recommendations = list(state.get("recommendations", []))
        confidence = float(state.get("confidence", 0.0))
        evidence = state.get("evidence", [])
        hitl_response = state.get("hitl_response")
        defect_type = state.get("defect_type") or None

        evidence_brief = [
            {
                "description": ev.get("description", ""),
                "source_tool": ev.get("source_tool", ""),
                "confidence": ev.get("confidence", 0.0),
            }
            for ev in evidence[:12]
        ]

        prompt = (
            f"用户问题：{state['user_query']}\n\n"
            f"【已确定根因（不可改写）】：{root_cause}\n"
            f"【已确定改进建议（不可改写）】：{json.dumps(recommendations, ensure_ascii=False)}\n"
            f"【置信度】：{confidence}\n"
            f"【证据条数】：{len(evidence)}\n"
            f"【HITL 人工反馈】：{hitl_response}\n\n"
            "请用 2-4 句话写一段分析摘要（非完整 8D），串联根因与关键证据。\n"
            "不得改写根因结论或建议列表。\n"
            '输出 JSON：{"summary": "..."}'
        )

        resp = await self.call_llm(
            system=PROMPT_PATH.read_text(encoding="utf-8"),
            messages=[{"role": "user", "content": prompt}],
            difficulty=TaskDifficulty.MODERATE,
            sensitivity=DataSensitivity.LOW,
            caller="reporter.run",
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")

        try:
            summary = json.loads(text).get("summary", text)
        except json.JSONDecodeError:
            summary = text[:800]

        rca_artifacts: dict[str, Any] = {
            "summary": summary,
            "root_cause": root_cause,
            "recommendations": recommendations,
            "confidence": confidence,
            "evidence_count": len(evidence),
            "defect_type": defect_type,
            "evidence": evidence_brief,
        }

        return {
            "root_cause": root_cause,
            "recommendations": recommendations,
            "final_report": summary,
            "rca_artifacts": rca_artifacts,
            "status": "done",
        }
