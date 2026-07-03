"""Neo4j 图客户端 — FMEA 因果树 + Golden Case 关联检索。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

try:
    from neo4j import AsyncGraphDatabase, Record
except ImportError:
    AsyncGraphDatabase = None  # type: ignore
    Record = None  # type: ignore


@dataclass
class Neo4jConfig:
    uri: str = "bolt://127.0.0.1:7687"
    user: str = "neo4j"
    password: str = "neo4jneo4j"
    database: str = "neo4j"


@dataclass
class GraphPath:
    """图检索结果 — 一条因果路径上的节点链。"""

    nodes: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    distance: int = 0
    source_case_id: str = ""
    target_case_id: str = ""


class Neo4jClient:
    """FMEA 因果图 + Golden Case 关联查询。

    用法:
        client = Neo4jClient()
        await client.connect()
        paths = await client.search_related_cases("涂布面密度偏差")
        await client.close()
    """

    def __init__(self, config: Neo4jConfig | None = None) -> None:
        self.config = config or Neo4jConfig()
        self._driver = None

    async def connect(self) -> None:
        if AsyncGraphDatabase is None:
            logger.warning("neo4j.unavailable", reason="neo4j driver not installed")
            return
        try:
            self._driver = AsyncGraphDatabase.driver(
                self.config.uri,
                auth=(self.config.user, self.config.password),
            )
            await self._driver.verify_connectivity()
            logger.info("neo4j.connected", uri=self.config.uri)
        except Exception as exc:
            logger.warning("neo4j.connect_failed", error=str(exc))
            self._driver = None

    async def search_related_cases(
        self,
        defect_type: str,
        max_depth: int = 4,
        limit: int = 10,
    ) -> list[GraphPath]:
        """从给定 defect_type 出发，沿 [:CAUSED_BY*] 向下找 RootCause，
        再沿 [:HAS_DEFECT] 关联到其他共享根因的 Golden Case。

        返回按因果距离排序的路径列表（短路径优先）。
        """
        if self._driver is None:
            logger.warning("neo4j.not_connected")
            return []

        query = """
        MATCH path = (d:Defect {type: $defect_type})-[:CAUSED_BY*1..$max_depth]->(rc:RootCause)
        MATCH (gc:GoldenCase)-[:HAS_DEFECT]->(d2:Defect)
        WHERE (d2)-[:CAUSED_BY*1..$max_depth]->(rc)
          AND gc.case_id <> d2.fallback  // 排除自身
        RETURN gc.case_id AS case_id,
               d2.type AS related_defect,
               rc.name AS shared_root_cause,
               rc.category AS root_cause_category,
               length(SHORTESTPATH((d)-[:CAUSED_BY*]->(rc))) +
               length(SHORTESTPATH((d2)-[:CAUSED_BY*]->(rc))) AS total_distance
        ORDER BY total_distance
        LIMIT $limit
        """
        try:
            async with self._driver.session(database=self.config.database) as session:
                result = await session.run(
                    query,
                    defect_type=defect_type,
                    max_depth=max_depth,
                    limit=limit,
                )
                records = await result.fetch()
                paths: list[GraphPath] = []
                for record in records:
                    data = record.data()
                    paths.append(GraphPath(
                        source_case_id=data.get("case_id", ""),
                        shared_root_cause=data.get("shared_root_cause", ""),
                        target_case_id=data.get("case_id", ""),
                        distance=data.get("total_distance", 0),
                    ))
                return paths
        except Exception as exc:
            logger.warning("neo4j.search_failed", error=str(exc))
            return []

    async def search_by_root_cause(
        self,
        root_cause_keywords: list[str],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """按根因关键词模糊匹配 RootCause 节点，关联出 Golden Case。"""
        if self._driver is None:
            return []

        query = """
        MATCH (rc:RootCause)
        WHERE ANY(kw IN $keywords => rc.name CONTAINS kw OR rc.name CONTAINS kw)
        MATCH (gc:GoldenCase)-[:HAS_DEFECT]->(:Defect)-[:CAUSED_BY*1..3]->(rc)
        RETURN gc.case_id AS case_id, rc.name AS root_cause,
               rc.category AS category,
               gc.defect_type AS defect_type
        LIMIT $limit
        """
        try:
            async with self._driver.session(database=self.config.database) as session:
                result = await session.run(
                    query,
                    keywords=root_cause_keywords,
                    limit=limit,
                )
                records = await result.fetch()
                return [r.data() for r in records]
        except Exception as exc:
            logger.warning("neo4j.search_root_cause_failed", error=str(exc))
            return []

    async def upsert_golden_case(self, case: dict[str, Any]) -> bool:
        """插入/更新一条 Golden Case 及其在图中的关联。

        CASE 节点 + HAS_DEFECT → Defect 节点。若 Defect 不存在则创建。
        """
        if self._driver is None:
            return False

        query = """
        MERGE (gc:GoldenCase {case_id: $case_id})
        SET gc.defect_type = $defect_type,
            gc.process = $process,
            gc.root_cause = $root_cause,
            gc.capa_highlight = $capa_highlight,
            gc.severity = $severity,
            gc.updated_at = $updated_at
        WITH gc
        MERGE (d:Defect {type: $defect_type})
        SET d.process = $process
        MERGE (gc)-[:HAS_DEFECT]->(d)
        RETURN gc.case_id AS case_id
        """
        try:
            async with self._driver.session(database=self.config.database) as session:
                result = await session.run(
                    query,
                    case_id=case["case_id"],
                    defect_type=case.get("defect_type", ""),
                    process=case.get("process", ""),
                    root_cause=case.get("root_cause", ""),
                    capa_highlight=case.get("capa_highlight", ""),
                    severity=case.get("severity", "medium"),
                    updated_at=case.get("updated_at", ""),
                )
                record = await result.single()
                return record is not None
        except Exception as exc:
            logger.warning("neo4j.upsert_failed", case_id=case.get("case_id"), error=str(exc))
            return False

    async def upsert_fmea_chain(
        self,
        defect_type: str,
        root_cause: str,
        cause_category: str,
        process: str,
        confidence: float = 0.8,
    ) -> bool:
        """插入 FMEA 因果链：Defect -[:CAUSED_BY]-> RootCause。"""
        if self._driver is None:
            return False

        query = """
        MERGE (d:Defect {type: $defect_type})
        SET d.process = $process
        MERGE (rc:RootCause {name: $root_cause})
        SET rc.category = $category,
            rc.process = $process
        MERGE (d)-[r:CAUSED_BY]->(rc)
        SET r.confidence = $confidence,
            r.updated_at = $updated_at
        RETURN d.type, rc.name
        """
        try:
            async with self._driver.session(database=self.config.database) as session:
                await session.run(
                    query,
                    defect_type=defect_type,
                    root_cause=root_cause,
                    category=cause_category,
                    process=process,
                    confidence=confidence,
                    updated_at=__import__("datetime").datetime.utcnow().isoformat() + "Z",
                )
                return True
        except Exception as exc:
            logger.warning("neo4j.fmea_chain_failed", error=str(exc))
            return False

    async def delete_all(self) -> None:
        """清空图数据（ETL 全量重建用）。"""
        if self._driver is None:
            return
        try:
            async with self._driver.session(database=self.config.database) as session:
                await session.run("MATCH (n) DETACH DELETE n")
                logger.info("neo4j.cleared")
        except Exception as exc:
            logger.warning("neo4j.clear_failed", error=str(exc))

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None
