"""MCP Knowledge Server — 混合检索（向量+图）为 RCA Agent 提供 few-shot 参考。

Tools:
  - search_fmea: FMEA 因果树检索（Neo4j 路径 + 关键词）
  - hybrid_search_golden_case: 向量+图混合检索历史 Golden Case
  - search_sop: SOP 作业指导书检索

连线架构:
  RCA Agent (Reflector节点)
      ↓ MCP tools/call
  knowledge MCP Server
      ├── MilvusClient (语义向量)
      └── Neo4jClient (FMEA 因果图)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from harness_core.retrieval.hybrid import HybridRetriever
from harness_core.retrieval.milvus_client import MilvusClient, MilvusConfig
from harness_core.retrieval.neo4j_client import Neo4jClient, Neo4jConfig

import structlog

logger = structlog.get_logger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────────────
MILVUS_HOST = os.getenv("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4jneo4j")
KNOWLEDGE_PORT = int(os.getenv("KNOWLEDGE_PORT", "8106"))
DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

mcp = FastMCP("knowledge_server", host="0.0.0.0", port=KNOWLEDGE_PORT)

# ── 运行时状态 ─────────────────────────────────────────────────────────────
_milvus: MilvusClient | None = None
_neo4j: Neo4jClient | None = None
_hybrid: HybridRetriever | None = None
_embed_fn_global = None  # lazy init


def _try_embed(text: str) -> list[float]:
    """尝试 embedding；无可用模型时返回空列表。
    支持: OpenAI / 本地 bge-m3 / 空跑 stub。
    """
    global _embed_fn_global
    if _embed_fn_global is None:
        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("OPENAI_BASE_URL", "")
        if api_key and base_url:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=api_key, base_url=base_url)

                def _embed_openai(t: str) -> list[float]:
                    resp = client.embeddings.create(
                        model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
                        input=t,
                    )
                    return resp.data[0].embedding

                _embed_fn_global = _embed_openai
                logger.info("embed.using_openai")
            except ImportError:
                logger.warning("embed.openai_not_installed")
        else:
            logger.warning("embed.not_configured")
            _embed_fn_global = lambda t: []  # stub

    return _embed_fn_global(text) if _embed_fn_global else []


async def _ensure_clients():
    global _milvus, _neo4j, _hybrid
    if _milvus is None:
        _milvus = MilvusClient(MilvusConfig(host=MILVUS_HOST, port=MILVUS_PORT))
        await _milvus.connect()
    if _neo4j is None:
        _neo4j = Neo4jClient(Neo4jConfig(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD))
        await _neo4j.connect()
    if _hybrid is None and _milvus and _neo4j:
        _hybrid = HybridRetriever(_milvus, _neo4j, embed_fn=_try_embed)


# ── MCP 接口定义 ──────────────────────────────────────────────────────────


class SearchFmeaInput(BaseModel):
    defect_type: str = Field("", description="缺陷类型，如 '涂布面密度偏差'")
    keyword: str = Field("", description="搜索关键词")


class HybridSearchGoldenCaseInput(BaseModel):
    symptom: str = Field(..., description="缺陷症状描述，如 '涂布面密度偏差 ±2%，连续 5 卷超阈值'")
    defect_type: str = Field("", description="明确缺陷类型（可选，提供后启用图检索）")
    top_k: int = Field(5, description="返回案例数")


class SearchSopInput(BaseModel):
    defect_type: str = Field("", description="缺陷类型")
    keyword: str = Field("", description="关键词")


# ── MCP Tools ─────────────────────────────────────────────────────────────


@mcp.tool()
async def search_fmea(defect_type: str = "", keyword: str = "") -> str:
    """FMEA 因果树检索 — 基于关键词或缺陷类型，从 Neo4j 检索因果链。

    Args:
        defect_type: 缺陷类型（可选）
        keyword: 搜索关键词（可选）
    """
    try:
        await _ensure_clients()
    except Exception as exc:
        logger.error("search_fmea.backend_error", error=str(exc))
        return json.dumps({"hits": [], "error": str(exc)}, ensure_ascii=False)

    if not _neo4j:
        return json.dumps({"hits": [], "note": "FMEA 服务未就绪"}, ensure_ascii=False)

    try:
        # 关键词 → 根因搜索
        if keyword:
            records = await _neo4j.search_by_root_cause(
                root_cause_keywords=[keyword],
                limit=10,
            )
            return json.dumps(
                {"source": "fmea_knowledge_graph", "hits": records}, ensure_ascii=False
            )

        # defect_type → 因果路径搜索
        if defect_type:
            paths = await _neo4j.search_related_cases(defect_type, max_depth=4, limit=10)
            hits = [
                {
                    "case_id": p.source_case_id or p.target_case_id,
                    "shared_root_cause": getattr(p, "shared_root_cause", ""),
                    "graph_distance": getattr(p, "distance", 0),
                }
                for p in paths
            ]
            return json.dumps(
                {
                    "source": "fmea_knowledge_graph",
                    "defect_type": defect_type,
                    "hits": hits,
                },
                ensure_ascii=False,
            )

        return json.dumps({"hits": [], "note": "请提供 defect_type 或 keyword"}, ensure_ascii=False)

    except Exception as exc:
        logger.error("search_fmea.error", error=str(exc))
        return json.dumps({"hits": [], "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def hybrid_search_golden_case(symptom: str, defect_type: str = "", top_k: int = 5) -> str:
    """向量 + 图混合检索历史 Golden Case。

    同时使用语义相似度（Milvus）和 FMEA 因果链（Neo4j）召回历史案例，
    按 RRF 融合排序。适合作为 RCA Agent 的 few-shot 参考。

    Args:
        symptom: 缺陷症状的自然语言描述，如 '涂布后面密度偏差超过±2%，连续5卷超出规格上限'
        defect_type: 缺陷类型（可选，提供后激活图检索通道）
        top_k: 返回案例数（默认 5）
    """
    await _ensure_clients()

    if not _hybrid:
        return json.dumps(
            {"reference_cases": [], "note": "混合检索未就绪（Milvus/Neo4j 不可用）"},
            ensure_ascii=False,
        )

    try:
        fused = await _hybrid.search(
            query_text=symptom,
            defect_type=defect_type,
            top_k=top_k,
        )
        return _hybrid.format_json_fewshot(fused, max_cases=top_k)
    except Exception as exc:
        logger.error("hybrid_search.error", error=str(exc))
        return json.dumps({"reference_cases": [], "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def search_sop(defect_type: str = "", keyword: str = "") -> str:
    """检索 SOP / 作业指导书片段。

    基于本地 JSON 索引 + 关键词匹配。
    P2 升级：SOP embedding → Milvus，支持语义搜索。

    Args:
        defect_type: 缺陷类型
        keyword: 关键词
    """
    sop_path = DATA_DIR / "sop_snippets.json"
    if not sop_path.exists():
        return json.dumps({"hits": [], "note": "SOP 索引文件不存在"})

    try:
        data = json.loads(sop_path.read_text(encoding="utf-8"))
        hits = []
        for item in data.get("items", []):
            if defect_type and defect_type not in item.get("defect_types", []):
                continue
            if keyword and keyword.lower() not in (item.get("title", "") + item.get("body", "")).lower():
                continue
            hits.append(item)
        return json.dumps({"hits": hits[:3], "source": "sop_index"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"hits": [], "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def health() -> dict[str, Any]:
    """Knowledge Server 健康检查 + 后端状态。"""
    statuses = {
        "milvus": "disconnected",
        "neo4j": "disconnected",
        "embedding": "unconfigured",
    }
    if _milvus:
        statuses["milvus"] = "connected" if _milvus._connected else "disconnected"
    if _neo4j:
        statuses["neo4j"] = "connected" if _neo4j._driver is not None else "disconnected"
    if _try_embed("ping"):
        statuses["embedding"] = "ready"
    return {"status": "ok", "service": "knowledge_server", "backends": statuses}


if __name__ == "__main__":
    mcp.run(transport="sse")
