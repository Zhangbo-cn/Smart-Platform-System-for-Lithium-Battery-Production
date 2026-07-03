from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from knowledge.fmea_tree import FMEANode, FMEATree

ROOT_PARENT = "__ROOT__"
CSV_COLUMNS = [
    "defect_type",
    "node_name",
    "parent_name",
    "tool",
    "tool_args_json",
    "metric_key",
    "direction",
    "threshold",
    "weight",
    "rec_immediate",
    "rec_long_term",
]


def tree_to_rows(tree: FMEATree) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(node: FMEANode, parent: str) -> None:
        direction, threshold = ("", "")
        if node.abnormal_when:
            direction, threshold = node.abnormal_when
        rows.append(
            {
                "defect_type": tree.defect_type,
                "node_name": node.name,
                "parent_name": parent,
                "tool": node.tool or "",
                "tool_args_json": json.dumps(node.tool_args, ensure_ascii=False),
                "metric_key": node.metric_key or "",
                "direction": direction,
                "threshold": threshold,
                "weight": node.weight,
                "rec_immediate": node.recommendation.get("immediate", ""),
                "rec_long_term": node.recommendation.get("long_term", ""),
            }
        )
        for child in node.children:
            walk(child, node.name)

    for branch in tree.root_branches:
        walk(branch, ROOT_PARENT)
    return rows


def write_csv(trees: dict[str, FMEATree], path: str | Path) -> int:
    path = Path(path)
    rows: list[dict[str, Any]] = []
    for tree in trees.values():
        rows.extend(tree_to_rows(tree))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _parse_row(row: dict[str, str]) -> tuple[str, str, str, FMEANode]:
    defect_type = row["defect_type"].strip()
    name = row["node_name"].strip()
    parent = row.get("parent_name", ROOT_PARENT).strip() or ROOT_PARENT

    tool_args: dict[str, Any] = {}
    raw_args = row.get("tool_args_json", "").strip()
    if raw_args:
        tool_args = json.loads(raw_args)

    abnormal_when = None
    direction = row.get("direction", "").strip()
    threshold_raw = row.get("threshold", "").strip()
    if direction and threshold_raw:
        abnormal_when = (direction, float(threshold_raw))

    recommendation: dict[str, str] = {}
    if row.get("rec_immediate", "").strip():
        recommendation["immediate"] = row["rec_immediate"].strip()
    if row.get("rec_long_term", "").strip():
        recommendation["long_term"] = row["rec_long_term"].strip()

    node = FMEANode(
        name=name,
        tool=row.get("tool", "").strip() or None,
        tool_args=tool_args,
        metric_key=row.get("metric_key", "").strip() or None,
        abnormal_when=abnormal_when,
        weight=float(row.get("weight") or 1.0),
        recommendation=recommendation,
    )
    return defect_type, parent, name, node


def load_trees_from_csv(path: str | Path) -> dict[str, FMEATree]:
    path = Path(path)
    if not path.exists():
        return {}

    by_defect: dict[str, dict[str, FMEANode]] = {}
    children_map: dict[str, list[tuple[str, str]]] = {}
    roots: dict[str, list[str]] = {}

    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            defect_type, parent, name, node = _parse_row(row)
            by_defect.setdefault(defect_type, {})[name] = node
            if parent == ROOT_PARENT:
                roots.setdefault(defect_type, []).append(name)
            else:
                children_map.setdefault(f"{defect_type}::{parent}", []).append(name)

    trees: dict[str, FMEATree] = {}
    for defect_type, nodes in by_defect.items():
        for parent_key, child_names in children_map.items():
            if not parent_key.startswith(f"{defect_type}::"):
                continue
            parent_name = parent_key.split("::", 1)[1]
            parent_node = nodes.get(parent_name)
            if parent_node is None:
                continue
            for child_name in child_names:
                child = nodes.get(child_name)
                if child:
                    parent_node.children.append(child)

        root_branches = [nodes[n] for n in roots.get(defect_type, []) if n in nodes]
        if root_branches:
            trees[defect_type] = FMEATree(defect_type=defect_type, root_branches=root_branches)
    return trees
