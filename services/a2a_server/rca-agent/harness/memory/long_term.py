from __future__ import annotations

from typing import Any

import structlog

from config import get_settings

logger = structlog.get_logger(__name__)


class LongTermMemory:
    """
    Long-term knowledge base for confirmed root-cause cases.

    Backends:
    - Milvus: vector retrieval over case descriptions
    - Neo4j: defect ↔ parameter ↔ equipment ↔ material knowledge graph

    The implementations are intentionally thin shims; production deployments
    plug in real clients (pymilvus, neo4j-async-driver) configured via env.
    """

    def __init__(
        self,
        milvus_client: Any | None = None,
        neo4j_driver: Any | None = None,
        embed_fn=None,
    ) -> None:
        self.settings = get_settings()
        self.milvus = milvus_client
        self.neo4j = neo4j_driver
        self.embed = embed_fn or (lambda text: [0.0] * 768)

    async def add_case(
        self,
        case_id: str,
        description: str,
        defect_type: str,
        root_cause: str,
        evidence: list[dict],
        confirmed_by: str,
        score: int,
    ) -> None:
        if score < 4:
            logger.info("case.skipped_low_score", case_id=case_id, score=score)
            return

        vector = self.embed(description)
        if self.milvus:
            self.milvus.insert(
                self.settings.milvus_collection,
                [{"case_id": case_id, "vector": vector, "description": description,
                  "defect_type": defect_type, "root_cause": root_cause}],
            )
        if self.neo4j:
            async with self.neo4j.session() as s:
                await s.run(
                    "MERGE (c:Case {id: $cid}) "
                    "SET c.defect = $defect, c.root_cause = $rc, c.score = $score",
                    cid=case_id, defect=defect_type, rc=root_cause, score=score,
                )

    async def search_similar_cases(
        self,
        query: str,
        defect_type: str | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        if not self.milvus:
            return []
        vector = self.embed(query)
        expr = f'defect_type == "{defect_type}"' if defect_type else None
        results = self.milvus.search(
            self.settings.milvus_collection,
            data=[vector],
            limit=top_k,
            expr=expr,
            output_fields=["case_id", "description", "root_cause"],
        )
        return [r for hits in results for r in hits]

    async def mark_negative_example(self, case_id: str, reason: str) -> None:
        if self.neo4j:
            async with self.neo4j.session() as s:
                await s.run(
                    "MATCH (c:Case {id: $cid}) SET c.negative = true, c.reason = $reason",
                    cid=case_id, reason=reason,
                )
