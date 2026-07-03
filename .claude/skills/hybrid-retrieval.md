# Hybrid Retrieval — Milvus + Neo4j 混合检索操作手册

Golden Case 向量+图混合检索系统的日常操作。数据在 `data/golden_cases.json`。

## 一、数据模型

每条 Golden Case 包含：
```
case_id | defect_type | process | severity | root_cause | capa_highlight 
symptoms | detection_method | fmea_causes[] | effect_verification
```

覆盖 15 条、12 种缺陷类型、9 道工序（匀浆→装配）。

## 二、ETL 管道

```bash
# 全量重建（清空 + 灌入）
python scripts/etl_golden_cases.py --rebuild

# 仅灌 Neo4j（FMEA 图节点）
python scripts/etl_golden_cases.py --neo4j-only --rebuild

# 仅灌 Milvus（需要 OPENAI_API_KEY）
OPENAI_API_KEY=sk-... python scripts/etl_golden_cases.py --milvus-only --rebuild

# 更新单条 case
python scripts/etl_golden_cases.py --case-id GC-8D-001

# 指定 embedding 模型
python scripts/etl_golden_cases.py --embed-model text-embedding-3-small --rebuild
```

环境变量：`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `EMBEDDING_MODEL`, `MILVUS_HOST`, `NEO4J_URI`

## 三、MCP Tools（knowledge Server :8106）

| Tool | 参数 | 说明 |
|------|------|------|
| `search_fmea` | defect_type, keyword | Neo4j 因果路径 / 关键词搜索 |
| `hybrid_search_golden_case` | symptom(必填), defect_type, top_k | **Milvus 向量 + Neo4j 图 RRF 融合** |
| `search_sop` | defect_type, keyword | SOP 关键词匹配（P2 升级语义搜索） |

### 混合检索原理

```
symptom 描述 → HybridRetriever
    ├── Milvus: embedding(symptom) → cosine 相似 top-K
    └── Neo4j: MATCH (d:Defect)-[:CAUSED_BY*1..4]->(rc)
               MATCH (gc:GoldenCase)-[:HAS_DEFECT]->(d2)-[:CAUSED_BY*1..4]->(rc)
    → RRF(Reciprocal Rank Fusion) 融合排序
    → format_json_fewshot(top=3)
```

### 测试命令

```bash
# 健康检查
curl http://127.0.0.1:8106/health

# 混合检索测试
curl -X POST http://127.0.0.1:8106/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool":"hybrid_search_golden_case","args":{"symptom":"涂布面密度偏差连续5卷超±2%","defect_type":"coating_uneven"}}'

# FMEA 搜索
curl -X POST http://127.0.0.1:8106/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool":"search_fmea","args":{"defect_type":"lithium_plating"}}'
```

## 四、Neo4j 图结构

### 节点类型
- `Defect {type, process}` — 缺陷类型
- `RootCause {name, category, process}` — 根因（category=equipment/material/process_parameter/environment/operation）
- `GoldenCase {case_id, defect_type, process, root_cause, capa_highlight}` — 案例

### 关系
- `(GoldenCase)-[:HAS_DEFECT]->(Defect)`
- `(Defect)-[:CAUSED_BY {confidence}]->(RootCause)`

### 常用查询（Neo4j Browser: http://localhost:7474）

```cypher
// 找某缺陷的所有因果路径
MATCH path = (d:Defect {type: 'coating_uneven'})-[:CAUSED_BY*1..4]->(rc:RootCause)
RETURN path LIMIT 20

// 找共享根因的案例
MATCH (gc:GoldenCase)-[:HAS_DEFECT]->(:Defect)-[:CAUSED_BY*]->(rc:RootCause)<-[:CAUSED_BY*]-(:Defect)<-[:HAS_DEFECT]-(gc2:GoldenCase)
WHERE gc.case_id <> gc2.case_id
RETURN gc.case_id, gc2.case_id, rc.name LIMIT 20

// 统计根因类别分布
MATCH (rc:RootCause)
RETURN rc.category, count(*) AS cnt ORDER BY cnt DESC

// 统计设备类根因
MATCH (rc:RootCause {category: 'equipment'})<-[:CAUSED_BY]-(d:Defect)
RETURN rc.name, d.type
```

## 五、Milvus 向量管理

```python
# Python: 检查数据
from pymilvus import Collection, connections
connections.connect(host="127.0.0.1", port="19530")
col = Collection("golden_cases")
col.load()
print(col.num_entities)  # 向量条数

# 搜索测试
results = col.search(
    data=[embedding_vector],
    anns_field="vector",
    param={"metric_type": "COSINE", "params": {"nprobe": 16}},
    limit=5,
    output_fields=["id", "defect_type", "process"],
)
```

## 六、Reporter Agent 集成

D5 子 Agent `d5_capa_planner` 已有 `hybrid_search_golden_case` 工具可用（`deep_agent_runner.py`），优先于旧版 JSON 关键词匹配。子 Agent 的 system prompt 已更新为：
> "优先使用 hybrid_search_golden_case 混合检索（向量+图）：它会同时基于语义相似和 FMEA 因果路径召回历史案例，比 search_golden_case 更全面。"

## 七、添加新 Golden Case

1. 在 `data/golden_cases.json` 中添加条目
2. 运行 `python scripts/etl_golden_cases.py --rebuild`
3. 验证检索效果：调用 `hybrid_search_golden_case` 确认新 case 可召回

## 八、架构文件

| 文件 | 说明 |
|------|------|
| `data/golden_cases.json` | 数据源 |
| `scripts/etl_golden_cases.py` | ETL 管道 |
| `packages/harness-core/retrieval/milvus_client.py` | Milvus 客户端 |
| `packages/harness-core/retrieval/neo4j_client.py` | Neo4j 客户端 |
| `packages/harness-core/retrieval/hybrid.py` | RRF 融合检索器 |
| `services/mcp/knowledge_server/knowledge_server.py` | MCP 知识服务 |
