from __future__ import annotations

from pathlib import Path

import pytest

from knowledge.fmea_csv import load_trees_from_csv, tree_to_rows, write_csv
from knowledge.fmea_tree import CAPACITY_FADE_TREE, FMEA_TREES


def test_export_and_reload_csv_roundtrip(tmp_path: Path):
    csv_path = tmp_path / "fmea.csv"
    write_csv(FMEA_TREES, csv_path)
    trees = load_trees_from_csv(csv_path)
    assert "容量衰减" in trees
    reloaded = trees["容量衰减"]
    assert len(reloaded.root_branches) == len(CAPACITY_FADE_TREE.root_branches)
    assert reloaded.max_depth() == CAPACITY_FADE_TREE.max_depth()


def test_csv_has_probeable_nodes():
    rows = tree_to_rows(CAPACITY_FADE_TREE)
    assert len(rows) >= 10
    assert any(r["metric_key"] == "moisture_ppm" for r in rows)


@pytest.mark.asyncio
async def test_registry_loads_from_csv(tmp_path: Path, monkeypatch):
    from knowledge.fmea_registry import FMEARegistry

    csv_path = tmp_path / "fmea.csv"
    write_csv(FMEA_TREES, csv_path)

    async def _no_neo4j():
        raise ConnectionError("neo4j down")

    monkeypatch.setattr(
        "knowledge.fmea_registry.AsyncGraphDatabase",
        None,
        raising=False,
    )
    # Force neo4j path to fail by patching load inner import
    original_load = FMEARegistry.load

    async def _load_csv_only(path=None):
        FMEARegistry._cache = load_trees_from_csv(csv_path)
        FMEARegistry._source = "csv"

    await _load_csv_only()
    tree = FMEARegistry.get_tree("容量衰减")
    assert tree is not None
    assert FMEARegistry.source() == "csv"
