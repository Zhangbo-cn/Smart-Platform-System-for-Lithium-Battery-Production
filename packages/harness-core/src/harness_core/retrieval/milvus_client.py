"""Milvus 向量索引客户端 — Golden Case / SOP 语义检索。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

try:
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
except ImportError:
    Collection = None  # type: ignore
    CollectionSchema = None
    DataType = None
    FieldSchema = None
    connections = None
    utility = None


@dataclass
class MilvusConfig:
    host: str = "127.0.0.1"
    port: str = "19530"
    collection: str = "golden_cases"
    dim: int = 768  # bge-m3 / text-embedding-3-small dimension


@dataclass
class VectorHit:
    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


class MilvusClient:
    """Golden Case 向量索引客户端。

    用法:
        client = MilvusClient()
        await client.connect()
        hits = await client.search("涂布面密度偏差 ±2%")
        await client.close()
    """

    def __init__(self, config: MilvusConfig | None = None) -> None:
        self.config = config or MilvusConfig()
        self._connected = False

    async def connect(self) -> None:
        if Collection is None:
            logger.warning("milvus.unavailable", reason="pymilvus not installed")
            return
        if self._connected:
            return
        try:
            connections.connect(host=self.config.host, port=self.config.port)
            self._ensure_collection()
            self._connected = True
            logger.info("milvus.connected", host=self.config.host, port=self.config.port)
        except Exception as exc:
            logger.warning("milvus.connect_failed", error=str(exc))

    def _ensure_collection(self) -> None:
        name = self.config.collection
        if utility and utility.has_collection(name):
            self._collection = Collection(name)
            self._collection.load()
            return

        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.config.dim),
            FieldSchema(name="defect_type", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="process", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="payload", dtype=DataType.VARCHAR, max_length=8192),
        ]
        schema = CollectionSchema(fields, description="锂电 Golden Case 向量索引")
        self._collection = Collection(name, schema)
        idx_params = {"metric_type": "COSINE", "index_type": "IVF_FLAT", "params": {"nlist": 128}}
        self._collection.create_index("vector", idx_params)
        self._collection.load()
        logger.info("milvus.collection_created", name=name)

    async def search(
        self,
        query_text: str,
        embed_fn: Any = None,
        top_k: int = 10,
        expr: str | None = None,
    ) -> list[VectorHit]:
        """向量检索。embed_fn 接收 str 返回 list[float]；
        未提供时返回空列表（需要外部 embedding 服务）。"""
        if Collection is None or not self._connected:
            logger.warning("milvus.not_connected")
            return []

        if embed_fn is None:
            logger.warning("milvus.no_embedding_fn")
            return []

        vector = embed_fn(query_text)
        if not vector:
            return []

        search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
        results = self._collection.search(
            data=[vector],
            anns_field="vector",
            param=search_params,
            limit=top_k,
            expr=expr,
            output_fields=["id", "defect_type", "process", "payload"],
        )

        hits: list[VectorHit] = []
        for hits_row in results:
            for hit in hits_row:
                payload_str = hit.entity.get("payload") or "{}"
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError:
                    payload = {}
                hits.append(VectorHit(
                    id=hit.id,
                    score=hit.score,
                    payload={
                        "defect_type": hit.entity.get("defect_type", ""),
                        "process": hit.entity.get("process", ""),
                        **payload,
                    },
                ))
        return hits

    async def upsert(self, items: list[dict[str, Any]], embed_fn: Any = None) -> int:
        """批量插入或更新 Golden Case 向量。"""
        if Collection is None or not self._connected:
            logger.warning("milvus.not_connected")
            return 0
        if embed_fn is None:
            logger.warning("milvus.no_embedding_fn")
            return 0

        rows: list[dict[str, Any]] = []
        for item in items:
            text_for_embedding = (
                f"缺陷类型：{item.get('defect_type', '')}。"
                f"根因：{item.get('root_cause', '')}。"
                f"改进措施：{item.get('capa_highlight', '')}。"
            )
            vector = embed_fn(text_for_embedding)
            if not vector:
                continue
            rows.append({
                "id": item["case_id"],
                "vector": vector,
                "defect_type": item.get("defect_type", ""),
                "process": item.get("process", ""),
                "payload": json.dumps(item, ensure_ascii=False),
            })

        if not rows:
            return 0

        self._collection.insert(rows)
        self._collection.flush()
        logger.info("milvus.upserted", count=len(rows))
        return len(rows)

    async def close(self) -> None:
        if Collection is not None and self._connected:
            connections.disconnect("default")
            self._connected = False
