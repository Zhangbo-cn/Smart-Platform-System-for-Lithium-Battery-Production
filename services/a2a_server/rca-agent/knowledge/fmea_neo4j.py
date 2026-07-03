from __future__ import annotations

import json
from typing import Any

from knowledge.fmea_csv import ROOT_PARENT, load_trees_from_csv, tree_to_rows
from knowledge.fmea_tree import FMEANode, FMEATree

FMEA_LABEL = "FMECause"
DEFECT_LABEL = "DefectType"


def _node_props(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": row["node_name"],
        "defect_type": row["defect_type"],
        "tool": row.get("tool") or "",
        "tool_args_json": row.get("tool_args_json") or "{}",
        "metric_key": row.get("metric_key") or "",
        "direction": row.get("direction") or "",
        "threshold": float(row["threshold"]) if str(row.get("threshold", "")).strip() else None,
        "weight": float(row.get("weight") or 1.0),
        "rec_immediate": row.get("rec_immediate") or "",
        "rec_long_term": row.get("rec_long_term") or "",
    }


async def import_csv_to_neo4j(driver, csv_path: str) -> dict[str, int]:
    """ETL: Excel/CSV FMEA 表 → Neo4j 因果图。工艺工程师维护 CSV，脚本导入图库。"""
    trees = load_trees_from_csv(csv_path)
    if not trees:
        return {"defects": 0, "nodes": 0}

    rows: list[dict[str, Any]] = []
    for tree in trees.values():
        rows.extend(tree_to_rows(tree))

    async with driver.session() as session:
        defect_types = list(trees.keys())
        # 仅清理本次 CSV 涉及的 FMEA 子图，保留目录中其他 DefectType / 工序 / MCP 节点
        await session.run(
            f"MATCH (n:{FMEA_LABEL}) WHERE n.defect_type IN $types DETACH DELETE n"
        , types=defect_types)
        for dt in defect_types:
            await session.run(
                f"""
                MATCH (d:{DEFECT_LABEL} {{name: $name}})-[r:HAS_ROOT_CAUSE]->()
                DELETE r
                """,
                name=dt,
            )

        for defect_type in trees:
            await session.run(
                f"MERGE (d:{DEFECT_LABEL} {{name: $name}}) SET d.fmea_tree = true",
                name=defect_type,
            )

        for row in rows:
            props = _node_props(row)
            await session.run(
                f"""
                MERGE (n:{FMEA_LABEL} {{name: $name, defect_type: $defect_type}})
                SET n += $props
                """,
                name=props["name"],
                defect_type=props["defect_type"],
                props=props,
            )

        for row in rows:
            parent = row["parent_name"]
            if parent == ROOT_PARENT:
                await session.run(
                    f"""
                    MATCH (d:{DEFECT_LABEL} {{name: $defect_type}})
                    MATCH (n:{FMEA_LABEL} {{name: $name, defect_type: $defect_type}})
                    MERGE (d)-[:HAS_ROOT_CAUSE]->(n)
                    """,
                    defect_type=row["defect_type"],
                    name=row["node_name"],
                )
            else:
                await session.run(
                    f"""
                    MATCH (p:{FMEA_LABEL} {{name: $parent, defect_type: $defect_type}})
                    MATCH (c:{FMEA_LABEL} {{name: $child, defect_type: $defect_type}})
                    MERGE (p)-[:HAS_CHILD]->(c)
                    """,
                    parent=parent,
                    child=row["node_name"],
                    defect_type=row["defect_type"],
                )

    return {"defects": len(trees), "nodes": len(rows)}


def _row_to_node(record: dict[str, Any]) -> FMEANode:
    tool_args = json.loads(record.get("tool_args_json") or "{}")
    abnormal_when = None
    direction = record.get("direction") or ""
    threshold = record.get("threshold")
    if direction and threshold is not None:
        abnormal_when = (direction, float(threshold))

    recommendation: dict[str, str] = {}
    if record.get("rec_immediate"):
        recommendation["immediate"] = record["rec_immediate"]
    if record.get("rec_long_term"):
        recommendation["long_term"] = record["rec_long_term"]

    return FMEANode(
        name=record["name"],
        tool=record.get("tool") or None,
        tool_args=tool_args,
        metric_key=record.get("metric_key") or None,
        abnormal_when=abnormal_when,
        weight=float(record.get("weight") or 1.0),
        recommendation=recommendation,
    )


async def load_tree_from_neo4j(driver, defect_type: str) -> FMEATree | None:
    """Agent 查图：从 Neo4j 还原 FMEATree，供 Reflector/FMEAValidator 消费。"""
    async with driver.session() as session:
        result = await session.run(
            f"""
            MATCH (d:{DEFECT_LABEL} {{name: $defect_type}})-[:HAS_ROOT_CAUSE]->(root:{FMEA_LABEL})
            RETURN root.name AS name
            """,
            defect_type=defect_type,
        )
        root_names = [r["name"] async for r in result]
        if not root_names:
            return None

        nodes_result = await session.run(
            f"""
            MATCH (n:{FMEA_LABEL} {{defect_type: $defect_type}})
            RETURN n {{.*, name: n.name}} AS node
            """,
            defect_type=defect_type,
        )
        records = [r["node"] async for r in nodes_result]

        child_result = await session.run(
            f"""
            MATCH (p:{FMEA_LABEL} {{defect_type: $defect_type}})-[:HAS_CHILD]->(c:{FMEA_LABEL})
            RETURN p.name AS parent, c.name AS child
            """,
            defect_type=defect_type,
        )
        children_map: dict[str, list[str]] = {}
        async for row in child_result:
            children_map.setdefault(row["parent"], []).append(row["child"])

    nodes = {rec["name"]: _row_to_node(rec) for rec in records}
    for parent, child_names in children_map.items():
        parent_node = nodes.get(parent)
        if not parent_node:
            continue
        for child_name in child_names:
            child = nodes.get(child_name)
            if child:
                parent_node.children.append(child)

    root_branches = [nodes[n] for n in root_names if n in nodes]
    if not root_branches:
        return None
    return FMEATree(defect_type=defect_type, root_branches=root_branches)


async def load_all_trees_from_neo4j(driver) -> dict[str, FMEATree]:
    async with driver.session() as session:
        result = await session.run(f"MATCH (d:{DEFECT_LABEL}) RETURN d.name AS name")
        defect_types = [r["name"] async for r in result]

    trees: dict[str, FMEATree] = {}
    for defect_type in defect_types:
        tree = await load_tree_from_neo4j(driver, defect_type)
        if tree:
            trees[defect_type] = tree
    return trees
