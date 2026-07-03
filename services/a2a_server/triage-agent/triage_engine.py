"""Triage 引擎：LLM 优先 + 规则兜底 — 多意图拆解 / 实体抽取 / 严重度判定。"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import structlog
from openai import OpenAI

from platform_contracts.agent_handoffs import TriageRequest, TriageResponse

logger = structlog.get_logger(__name__)

_SERVICE = "triage-agent"

# ── 规则兜底 ──────────────────────────────────────────────

_KEYWORD_DEFECTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"涂布|面密度|coating", re.I), "coating_density_low"),
    (re.compile(r"容量|capacity|衰减|循环", re.I), "capacity_low"),
    (re.compile(r"内阻|阻抗|ir|电阻", re.I), "ir_high"),
    (re.compile(r"短路|short|微短|刺穿", re.I), "short_circuit"),
    (re.compile(r"析锂|lithium.*plat|锂枝晶", re.I), "lithium_plating"),
    (re.compile(r"注液|电解液|浸润|leak|漏液", re.I), "electrolyte_leakage"),
    (re.compile(r"鼓胀|swelling|变形|凸起", re.I), "swelling"),
    (re.compile(r"虚焊|焊接|焊点|极耳|weld", re.I), "weld_defect"),
    (re.compile(r"毛刺|burr|分切|cutting", re.I), "burr_excessive"),
    (re.compile(r"水分|湿度|moisture|ppm.*水", re.I), "moisture_exceed"),
    (re.compile(r"隔膜|separator|闭孔|收缩", re.I), "separator_defect"),
    (re.compile(r"辊压|压实|孔隙率|density|过压", re.I), "rolling_overpressure"),
    (re.compile(r"搅拌|分散|团聚|disperse|浆料|slurry", re.I), "mixing_uneven"),
    (re.compile(r"烘箱|oven|温度.*高|干燥|curing", re.I), "drying_abnormal"),
    (re.compile(r"来料|ncm|正极|负极|anode|cathode|材料", re.I), "raw_material_issue"),
    (re.compile(r"化成|sei|formation|老化|aging", re.I), "formation_abnormal"),
]

_SEVERITY_KEYWORDS: list[tuple[re.Pattern[str], Literal["critical", "high", "medium", "low"]]] = [
    (re.compile(r"起火|爆炸|安全|smoke|fire|安全|safety", re.I), "critical"),
    (re.compile(r"批量|整批|全部|大批|all.*batch|全线", re.I), "high"),
    (re.compile(r"历史复现|再发|repeat|again|recur|又出现", re.I), "high"),
    (re.compile(r"个别|偶发|偶尔|sporadic|once", re.I), "low"),
]

_BATCH_PATTERN = re.compile(r"[A-Z]\d{6,}")

_MULTI_INTENT_SEPARATORS = re.compile(r"[。；;]|而且|并且|还有|另外|同时")


def _rule_defect(query: str) -> str:
    for pattern, defect in _KEYWORD_DEFECTS:
        if pattern.search(query):
            return defect
    return "unknown_defect"


def _rule_severity(query: str, defect: str) -> Literal["low", "medium", "high", "critical"]:
    for pattern, sev in _SEVERITY_KEYWORDS:
        if pattern.search(query):
            return sev
    # 默认：已知缺陷 → medium，未知 → low
    return "medium" if defect != "unknown_defect" else "low"


def _extract_batch_ids(query: str) -> list[str]:
    return _BATCH_PATTERN.findall(query)


def _split_intents(query: str) -> list[str]:
    """将用户的多意图查询拆成单意图子句。"""
    parts = _MULTI_INTENT_SEPARATORS.split(query)
    return [p.strip() for p in parts if p.strip()]


def _rule_triage(req: TriageRequest) -> TriageResponse:
    """纯规则兜底。"""
    query = req.query or ""
    defect = _rule_defect(query)
    severity = _rule_severity(query, defect)
    batch_ids = _extract_batch_ids(query)
    suggest: Literal["trace", "rca", "none"] = "trace" if (req.batch_id or batch_ids) else "rca"
    return TriageResponse(
        defect_type=defect,
        severity=severity,
        suggest_next=suggest,
        stub=True,
    )


# ── LLM 分诊 prompt ───────────────────────────────────────

_SYSTEM_PROMPT = """你是一个锂电池制造领域的异常分诊专家。你的任务是从用户的描述中提取结构化信息。

返回 JSON（不要 markdown 包裹）：
{
  "defect_type": "缺陷类型英文slug，如 coating_density_low / capacity_low / ir_high / short_circuit / lithium_plating / electrolyte_leakage / swelling / weld_defect / burr_excessive / moisture_exceed / separator_defect / rolling_overpressure / mixing_uneven / drying_abnormal / raw_material_issue / formation_abnormal / unknown_defect",
  "severity": "low | medium | high | critical",
  "suggest_next": "trace | rca | none",
  "extracted_batch_ids": ["从文本中提取的批次号数组"],
  "multi_intents": ["如果用户描述了多个问题，拆成数组；单意图时只有1个元素"],
  "confidence": 0.0-1.0
}

规则：
- 有 batch_id 或能提取批次号 → suggest_next = trace
- 没有 batch_id 但描述了缺陷症状 → suggest_next = rca
- 历史复现/批量异常 → severity = high
- 起火/安全风险 → severity = critical
- 不确定时 severity = medium、defect_type = unknown_defect
- confidence < 0.5 时防御性返回规则结果
"""


async def _llm_triage(req: TriageRequest, api_key: str, model: str, base_url: str) -> TriageResponse | None:
    """尝试 LLM 分诊。失败返回 None 触发规则兜底。"""
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "query": req.query,
                            "batch_id": req.batch_id,
                            "session_id": req.session_id,
                        }
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        if not raw:
            return None
        data = json.loads(raw)
        confidence = data.get("confidence", 0.0)
        if confidence < 0.5:
            logger.warning("triage.low_confidence", confidence=confidence, raw=raw)
            return None

        sev: Literal["low", "medium", "high", "critical"] = data.get("severity", "medium")
        suggest: Literal["trace", "rca", "none"] = data.get("suggest_next", "rca")
        return TriageResponse(
            defect_type=data.get("defect_type", "unknown_defect"),
            severity=sev,
            suggest_next=suggest,
            stub=False,
        )
    except Exception as exc:
        logger.warning("triage.llm_failed", error=str(exc))
        return None


# ── 入口 ──────────────────────────────────────────────────


async def run_triage(
    req: TriageRequest,
    *,
    llm_api_key: str = "",
    llm_model: str = "deepseek-chat",
    llm_base_url: str = "https://api.deepseek.com",
) -> TriageResponse:
    """LLM 优先 → rule 兜底。"""
    intents = _split_intents(req.query or "")
    logger.info("triage.run", session_id=req.session_id, intents_count=len(intents), query=req.query)

    # LLM 尝试
    llm_result = None
    if llm_api_key:
        llm_result = await _llm_triage(req, llm_api_key, llm_model, llm_base_url)

    if llm_result and not llm_result.stub:
        logger.info("triage.llm_ok", defect=llm_result.defect_type, severity=llm_result.severity)
        return llm_result

    # 规则兜底
    rule_result = _rule_triage(req)
    logger.info(
        "triage.rule_fallback",
        defect=rule_result.defect_type,
        severity=rule_result.severity,
        llm_attempted=bool(llm_api_key),
    )
    return rule_result
