"""Export built-in FMEA trees to Excel-editable CSV.

Usage:
    python -m scripts.export_fmea_csv
"""
from __future__ import annotations

from pathlib import Path

from knowledge.fmea_csv import write_csv
from knowledge.fmea_tree import FMEA_TREES

OUT = Path(__file__).parent.parent / "knowledge" / "fmea_source.csv"


def main() -> None:
    count = write_csv(FMEA_TREES, OUT)
    print(f"Exported {count} FMEA rows -> {OUT}")
    print("工艺工程师可在 Excel 中维护此表，再执行: python -m scripts.import_fmea_to_neo4j")


if __name__ == "__main__":
    main()
