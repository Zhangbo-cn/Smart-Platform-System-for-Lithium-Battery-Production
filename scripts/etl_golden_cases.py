#!/usr/bin/env python3
"""Golden Case ETL：Golden Case 灌入 Milvus 向量索引 + Neo4j 知识图谱。

用法:
    # 全量重建（先清空再灌入）
    python scripts/etl_golden_cases.py --rebuild

    # 仅更新指定 case
    python scripts/etl_golden_cases.py --case-id GC-8D-001

    # 仅灌 Neo4j（跳过 Milvus）
    python scripts/etl_golden_cases.py --neo4j-only

    # 仅灌 Milvus（跳过 Neo4j）
    python scripts/etl_golden_cases.py --milvus-only

    # 使用指定 embedding 模型
    python scripts/etl_golden_cases.py --embed-model text-embedding-3-small

环境变量:
    OPENAI_API_KEY / OPENAI_BASE_URL — embedding 用
    EMBEDDING_MODEL — 模型名（默认 text-embedding-3-small）
    MILVUS_HOST / MILVUS_PORT
    NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# 将项目根加入 sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── lazy import ──────────────────────────────────────────────────────────


def _init_embedding(model_name: str = "text-embedding-3-small"):
    """初始化 embedding 函数。"""
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "")
    if not api_key:
        print("⚠ WARNING: OPENAI_API_KEY 未设置，使用零向量 embedding（仅用于测试）")
        return lambda t: [0.0] * 768

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)

        def _embed(text: str) -> list[float]:
            resp = client.embeddings.create(model=model_name, input=text)
            return resp.data[0].embedding

        print(f"✅ embedding 就绪: model={model_name}")
        return _embed
    except ImportError:
        print("⚠ openai 未安装，使用零向量 stub")
        return lambda t: [0.0] * 768


def load_cases(path: Path) -> list[dict]:
    """加载 Golden Case JSON。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    print(f"📄 加载 Golden Case: {len(cases)} 条 (schema={data.get('schema_version')})")
    return cases


# ── Milvus ───────────────────────────────────────────────────────────────


async def etl_milvus(cases: list[dict], embed_fn, rebuild: bool = False):
    """Golden Case → Milvus 向量索引。"""
    from harness_core.retrieval.milvus_client import MilvusClient, MilvusConfig

    config = MilvusConfig(
        host=os.getenv("MILVUS_HOST", "127.0.0.1"),
        port=os.getenv("MILVUS_PORT", "19530"),
    )
    client = MilvusClient(config)
    await client.connect()

    if rebuild:
        # 重建 collection
        print("🔄 重建 Milvus collection ...")
        try:
            from pymilvus import utility
            if utility and utility.has_collection(config.collection):
                utility.drop_collection(config.collection)
                print("  已删除旧 collection")
        except Exception:
            pass
        client._connected = False
        await client.connect()

    count = await client.upsert(cases, embed_fn=embed_fn)
    print(f"✅ Milvus 写入: {count} 条")
    await client.close()


# ── Neo4j ────────────────────────────────────────────────────────────────


async def etl_neo4j(cases: list[dict], rebuild: bool = False):
    """Golden Case → Neo4j 图数据库（节点 + 关系）。"""
    from harness_core.retrieval.neo4j_client import Neo4jClient, Neo4jConfig

    config = Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "neo4jneo4j"),
    )
    client = Neo4jClient(config)
    await client.connect()

    if rebuild:
        print("🔄 清空 Neo4j 图 ...")
        await client.delete_all()

    # 1. 灌 GoldenCase + HAS_DEFECT
    success = 0
    for case in cases:
        ok = await client.upsert_golden_case(case)
        if ok:
            success += 1
    print(f"✅ Neo4j GoldenCase 节点: {success}/{len(cases)}")

    # 2. 建 FMEA 因果链 (Defect → RootCause)
    fmea_count = 0
    for case in cases:
        defect_type = case.get("defect_type", "")
        process = case.get("process", "")
        for cause_text in case.get("fmea_causes", []):
            # 从 fmea_causes 推导类别
            category = "unknown"
            if "设备" in cause_text or "磨损" in cause_text or "老化" in cause_text or "钝化" in cause_text:
                category = "equipment"
            elif "来料" in cause_text or "供应商" in cause_text or "批次" in cause_text:
                category = "material"
            elif "参数" in cause_text or "设定" in cause_text:
                category = "process_parameter"
            elif "环境" in cause_text or "温度" in cause_text or "洁净度" in cause_text:
                category = "environment"
            elif "操作" in cause_text or "班组长" in cause_text or "SOP" in cause_text:
                category = "operation"

            ok = await client.upsert_fmea_chain(
                defect_type=defect_type,
                root_cause=cause_text,
                cause_category=category,
                process=process,
                confidence=0.8,
            )
            if ok:
                fmea_count += 1
    print(f"✅ Neo4j FMEA 因果链: {fmea_count} 条")

    await client.close()


# ── ETL 报告 ─────────────────────────────────────────────────────────────


def print_report(cases: list[dict]):
    """打印数据概览。"""
    defects = {}
    processes = set()
    severities = set()
    for c in cases:
        d = c.get("defect_type", "unknown")
        defects[d] = defects.get(d, 0) + 1
        p = c.get("process", "")
        if p:
            processes.add(p)
        s = c.get("severity", "")
        if s:
            severities.add(s)

    print(f"\n📊 ETL 数据概览:")
    print(f"  总案例数: {len(cases)}")
    print(f"  覆盖工序: {', '.join(sorted(processes))}")
    print(f"  缺陷类型: {', '.join(f'{k}({v})' for k, v in sorted(defects.items()))}")
    print(f"  严重度:   {', '.join(sorted(severities))}")
    print(f"  FMEA 原因: {sum(len(c.get('fmea_causes', [])) for c in cases)} 条")


# ── main ──────────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="Golden Case ETL")
    parser.add_argument("--rebuild", action="store_true", help="全量重建索引")
    parser.add_argument("--milvus-only", action="store_true", help="仅灌 Milvus")
    parser.add_argument("--neo4j-only", action="store_true", help="仅灌 Neo4j")
    parser.add_argument("--case-id", help="仅更新指定 case")
    parser.add_argument(
        "--embed-model",
        default=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        help="Embedding 模型名",
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=ROOT / "data" / "golden_cases.json",
        help="Golden Case JSON 路径",
    )
    args = parser.parse_args()

    # 加载数据
    if not args.data_path.exists():
        print(f"❌ 数据文件不存在: {args.data_path}")
        sys.exit(1)

    cases = load_cases(args.data_path)

    # 过滤指定 case
    if args.case_id:
        cases = [c for c in cases if c["case_id"] == args.case_id]
        if not cases:
            print(f"❌ 未找到 case: {args.case_id}")
            sys.exit(1)
        print(f"🔍 仅处理: {args.case_id}")

    print_report(cases)

    # 初始化 embedding
    embed_fn = _init_embedding(args.embed_model)

    # 执行 ETL
    do_milvus = not args.neo4j_only
    do_neo4j = not args.milvus_only

    if do_milvus:
        print("\n── Milvus 索引 ──")
        await etl_milvus(cases, embed_fn, rebuild=args.rebuild)

    if do_neo4j:
        print("\n── Neo4j 图索引 ──")
        await etl_neo4j(cases, rebuild=args.rebuild)

    print("\n🎉 ETL 完成")


if __name__ == "__main__":
    asyncio.run(main())
