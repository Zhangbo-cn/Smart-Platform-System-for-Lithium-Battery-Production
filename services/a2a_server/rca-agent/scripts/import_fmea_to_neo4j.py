"""ETL: Excel/CSV FMEA 表 → Neo4j 因果图。

Usage:
    python -m scripts.export_fmea_csv          # 首次从内置树导出 CSV
    # 工艺工程师在 Excel 中编辑 knowledge/fmea_source.csv
    python -m scripts.import_fmea_to_neo4j     # 导入 Neo4j

Requires Neo4j running (see deploy/docker-compose.yml).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from config import get_settings
from knowledge.fmea_neo4j import import_csv_to_neo4j

CSV_PATH = Path(__file__).parent.parent / "knowledge" / "fmea_source.csv"


async def main() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"CSV not found: {CSV_PATH}. Run: python -m scripts.export_fmea_csv")

    settings = get_settings()
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        stats = await import_csv_to_neo4j(driver, str(CSV_PATH))
        print(f"Imported FMEA to Neo4j: {stats}")
    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(main())
