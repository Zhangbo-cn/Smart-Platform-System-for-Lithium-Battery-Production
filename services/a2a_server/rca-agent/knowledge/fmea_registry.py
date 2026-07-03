from __future__ import annotations

from pathlib import Path

import structlog

from config import get_settings
from knowledge.fmea_csv import load_trees_from_csv
from knowledge.fmea_tree import FMEA_TREES, FMEATree

logger = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CSV = _PROJECT_ROOT / "knowledge" / "fmea_source.csv"


class FMEARegistry:
    """
    FMEA 知识统一入口：Neo4j 图库 > Excel/CSV 导出表 > Python 内置树。

    工业界常见链路：工艺工程师维护 Excel → 导出 CSV → ETL 入 Neo4j → Agent 查图。
    """

    _cache: dict[str, FMEATree] = {}
    _source: str = "builtin"

    @classmethod
    def get_tree(cls, defect_type: str) -> FMEATree | None:
        return cls._cache.get(defect_type) or FMEA_TREES.get(defect_type)

    @classmethod
    def source(cls) -> str:
        return cls._source

    @classmethod
    def list_defect_types(cls) -> list[str]:
        keys = set(FMEA_TREES) | set(cls._cache)
        return sorted(keys)

    @classmethod
    async def load(cls, csv_path: str | Path | None = None) -> None:
        csv_path = Path(csv_path or _DEFAULT_CSV)
        settings = get_settings()

        # 1) Neo4j 优先（Agent 查图）
        try:
            from neo4j import AsyncGraphDatabase

            driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
            from knowledge.fmea_neo4j import load_all_trees_from_neo4j

            trees = await load_all_trees_from_neo4j(driver)
            await driver.close()
            if trees:
                cls._cache = trees
                cls._source = "neo4j"
                logger.info("fmea.loaded", source="neo4j", defects=len(trees))
                return
        except Exception as exc:
            logger.warning("fmea.neo4j_unavailable", error=str(exc))

        # 2) CSV/Excel 导出表（无需 Neo4j 亦可跑）
        if csv_path.exists():
            trees = load_trees_from_csv(csv_path)
            if trees:
                cls._cache = trees
                cls._source = "csv"
                logger.info("fmea.loaded", source="csv", path=str(csv_path), defects=len(trees))
                return

        # 3) 内置 Python 树（开发兜底）
        cls._cache = dict(FMEA_TREES)
        cls._source = "builtin"
        logger.info("fmea.loaded", source="builtin", defects=len(cls._cache))


def get_tree(defect_type: str) -> FMEATree | None:
    return FMEARegistry.get_tree(defect_type)
