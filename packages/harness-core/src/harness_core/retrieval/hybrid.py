"""向量 + 图 混合检索（RRF 融合）→ few-shot 上下文。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import structlog

from harness_core.retrieval.milvus_client import MilvusClient, MilvusConfig, VectorHit
from harness_core.retrieval.neo4j_client import Neo4jClient, Neo4jConfig

logger = structlog.get_logger(__name__)


@dataclass
class FusedHit:
    """融合后的 Golden Case 命中结果。"""

    case_id: str
    rrf_score: float
    milvus_score: float = 0.0
    milvus_rank: int = 999
    neo4j_rank: int = 999
    graph_distance: int = 999
    shared_root_cause: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class HybridRetriever:
    """混合检索器：并行跑 Milvus 向量 + Neo4j 图，RRF 融合。

    用法:
        retriever = HybridRetriever(milvus, neo4j, embed_fn)
        fused = await retriever.search("涂布面密度偏差 ±2%", defect_type="涂布面密度偏差")
        ctx = retriever.format_fewshot(fused, max_cases=3)
    """

    def __init__(
        self,
        milvus: MilvusClient,
        neo4j: Neo4jClient,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self.milvus = milvus
        self.neo4j = neo4j
        self.embed_fn = embed_fn

    async def search(
        self,
        query_text: str,
        defect_type: str = "",
        top_k: int = 10,
    ) -> list[FusedHit]:
        """并行执行向量 + 图检索，RRF 融合排序。"""
        # ---- 通道 A：Milvus 语义相似 ----
        vec_hits: list[VectorHit] = []
        if self.milvus and self.embed_fn:
            try:
                vec_hits = await self.milvus.search(query_text, self.embed_fn, top_k=top_k)
            except Exception as exc:
                logger.warning("hybrid.milvus_failed", error=str(exc))

        # ---- 通道 B：Neo4j 图结构 ----
        graph_paths: list[Any] = []
        if self.neo4j and defect_type:
            try:
                graph_paths = await self.neo4j.search_related_cases(defect_type, limit=top_k)
            except Exception as exc:
                logger.warning("hybrid.neo4j_failed", error=str(exc))

        # ---- RRF 融合 (Reciprocal Rank Fusion) ----
        k = 60  # RRF 常数

        # 建立 case_id → FusedHit 映射
        hits_map: dict[str, FusedHit] = {}

        # 向量通道赋秩分
        for rank, hit in enumerate(vec_hits, start=1):
            case_id = hit.id
            hits_map[case_id] = FusedHit(
                case_id=case_id,
                rrf_score=1.0 / (k + rank),
                milvus_score=hit.score,
                milvus_rank=rank,
                payload=hit.payload,
            )

        # 图通道赋秩分（按因果距离排序后）
        seen_graph_ids: set[str] = set()
        sorted_graph = sorted(graph_paths, key=lambda p: p.distance if hasattr(p, "distance") else 999)
        for rank, path in enumerate(sorted_graph, start=1):
            case_id = path.source_case_id or path.target_case_id
            if case_id in seen_graph_ids:
                continue
            seen_graph_ids.add(case_id)
            if case_id in hits_map:
                hits_map[case_id].rrf_score += 1.0 / (k + rank)
                hits_map[case_id].neo4j_rank = rank
                hits_map[case_id].graph_distance = getattr(path, "distance", 999)
                hits_map[case_id].shared_root_cause = getattr(path, "shared_root_cause", "")
            else:
                hits_map[case_id] = FusedHit(
                    case_id=case_id,
                    rrf_score=1.0 / (k + rank),
                    neo4j_rank=rank,
                    graph_distance=getattr(path, "distance", 999),
                    shared_root_cause=getattr(path, "shared_root_cause", ""),
                    payload={"case_id": case_id},
                )

        # 按 RRF 得分降序排列
        fused = sorted(hits_map.values(), key=lambda h: h.rrf_score, reverse=True)
        return fused[:top_k]

    def format_fewshot(
        self,
        hits: list[FusedHit],
        max_cases: int = 3,
    ) -> str:
        """将融合结果格式化为 LLM 可读的 few-shot 上下文。

        输出 Markdown 表格，有图路径来源的会标记「共享根因」。
        """
        if not hits:
            return ""

        lines = [
            "## 参考历史案例（混合检索：向量语义 + FMEA 因果链）",
            "",
        ]
        for i, hit in enumerate(hits[:max_cases], start=1):
            p = hit.payload
            case_id = hit.case_id
            source_tags = []
            if hit.milvus_rank < 999:
                source_tags.append(f"语义相似度 {hit.milvus_score:.3f}")
            if hit.neo4j_rank < 999:
                tag = f"因果路径距离={hit.graph_distance}"
                if hit.shared_root_cause:
                    tag += f"（共享根因：{hit.shared_root_cause}）"
                source_tags.append(tag)

            lines.append(f"### 案例 #{i} — {case_id}")
            lines.append(f"| 来源 | {', '.join(source_tags)} |")
            lines.append("|------|------|")
            lines.append(f"| 缺陷类型 | {p.get('defect_type', 'N/A')} |")
            lines.append(f"| 工序 | {p.get('process', 'N/A')} |")
            lines.append(f"| 严重度 | {p.get('severity', 'N/A')} |")
            lines.append(f"| 根因 | {p.get('root_cause', 'N/A')} |")
            lines.append(f"| 改进措施 | {p.get('capa_highlight', 'N/A')} |")
            if hit.shared_root_cause:
                lines.append(f"| 共享根因 | {hit.shared_root_cause} |")
            lines.append("")

        lines.append("--- 参考案例结束 ---")
        return "\n".join(lines)

    def format_json_fewshot(self, hits: list[FusedHit], max_cases: int = 3) -> str:
        """JSON 格式的 few-shot（适合结构化注入）。"""
        cases = []
        for hit in hits[:max_cases]:
            p = hit.payload
            cases.append({
                "case_id": hit.case_id,
                "defect_type": p.get("defect_type", ""),
                "process": p.get("process", ""),
                "severity": p.get("severity", ""),
                "root_cause": p.get("root_cause", ""),
                "capa_highlight": p.get("capa_highlight", ""),
                "milvus_similarity": round(hit.milvus_score, 3) if hit.milvus_rank < 999 else None,
                "shared_root_cause": hit.shared_root_cause or None,
                "graph_distance": hit.graph_distance if hit.neo4j_rank < 999 else None,
            })
        return json.dumps({"reference_cases": cases}, ensure_ascii=False, indent=2)
