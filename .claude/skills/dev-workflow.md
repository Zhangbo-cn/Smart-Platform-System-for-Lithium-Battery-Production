# Dev Workflow — 锂电平台开发命令速查

## 一、Docker 环境

```bash
# 构建基础镜像（首次 / requirements 变更后）
docker build -t battery-agent-base .

# 启动平台（11 服务）
docker compose -f deploy/docker-compose.platform.yml up -d

# 启动平台 + MCP（17 服务）
docker compose -f deploy/docker-compose.platform.yml --profile mcp up -d

# 仅基础设施（开发时手动起 Agent）
docker compose -f deploy/docker-compose.platform.yml up -d redis postgres neo4j

# 查看所有容器状态
docker compose -f deploy/docker-compose.platform.yml ps

# 查看日志
docker compose -f deploy/docker-compose.platform.yml logs -f orchestrator
docker compose -f deploy/docker-compose.platform.yml logs -f reporter-agent
docker compose -f deploy/docker-compose.platform.yml logs -f mcp-knowledge

# 停止
docker compose -f deploy/docker-compose.platform.yml down

# 跳过 RCA Agent（目录不存在时）
docker compose -f deploy/docker-compose.platform.yml up -d --scale rca-agent=0
```

## 二、启动顺序（手动开发）

```bash
# 1. 基础设施
docker compose -f deploy/docker-compose.platform.yml up -d redis postgres neo4j

# 2. 控制面
cd services/capability-registry && uvicorn app:app --port 8021 --reload
cd services/orchestrator && uvicorn app:app --port 8020 --reload
cd services/client-gateway && uvicorn app:app --port 8010 --reload

# 3. 业务 Agent
cd services/a2a_server/trace_worker && uvicorn app:app --port 8002 --reload
cd services/a2a_server/report-agent && uvicorn app:app --port 8004 --reload

# 4. MCP（可选）
cd services/mcp && python -m mes_server.mes_server     # 8101
cd services/mcp && python -m knowledge_server.knowledge_server  # 8106
```

**PYTHONPATH 环境变量**（手动起时需要）：
```bash
export PYTHONPATH=/app/packages/platform-contracts/src:/app/packages/harness-core/src
```

## 三、ETL 管道

```bash
# 全量重建 Golden Case 索引（Milvus + Neo4j）
python scripts/etl_golden_cases.py --rebuild

# 仅灌 Neo4j
python scripts/etl_golden_cases.py --neo4j-only --rebuild

# 仅灌 Milvus（需要 OPENAI_API_KEY）
OPENAI_API_KEY=sk-... python scripts/etl_golden_cases.py --milvus-only --rebuild

# 仅更新单个 case
python scripts/etl_golden_cases.py --case-id GC-8D-001
```

数据源：`data/golden_cases.json`（15 条）

## 四、API 测试

```bash
# 健康检查
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8020/health
curl http://127.0.0.1:8106/health    # knowledge MCP

# Orchestrator 全链路 close_loop
curl -X POST http://127.0.0.1:8020/a2a/v1/router/dispatch \
  -H "Content-Type: application/json" \
  -d '{"playbook":"close_loop","batch_id":"B20260630","message":"涂布面密度偏差±2%","confirm_rca":true,"hitl_approved":true}'

# 仅 RCA
curl -X POST http://127.0.0.1:8020/a2a/v1/router/dispatch \
  -H "Content-Type: application/json" \
  -d '{"playbook":"rca","batch_id":"B001","message":"容量偏低","confirm_rca":true}'

# 混合检索测试（需 knowledge MCP 运行）
curl -X POST http://127.0.0.1:8106/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool":"hybrid_search_golden_case","args":{"symptom":"涂布面密度偏差±2%","defect_type":"coating_uneven"}}'
```

## 五、评测

```bash
# 全部评测（RCA 10 例 + Reporter 15 例）
python scripts/run_eval.py --all

# 仅 RCA
python scripts/run_eval.py --rca

# 仅 Reporter
python scripts/run_eval.py --reporter

# Prompt 回归测试（对比新旧版本）
python scripts/run_eval.py --prompt-regression
```

Golden Set 配置：`packages/eval-core/prompts/registry.yaml`

## 六、常用文件路径

| 用途 | 路径 |
|------|------|
| 架构文档 | `docs/` |
| Agent 注册表 | `docs/AGENT_CATALOG.md` |
| MCP 工具矩阵 | `packages/platform-contracts/mcp_tool_matrix.py` |
| Playbook 配置 | `config/playbooks.yaml` |
| Golden Case 数据 | `data/golden_cases.json` |
| RCA review skill | `.claude/skills/rca-review.md` |
| Orchestrator review | `.claude/skills/orchestrator-review.md` |
| Golden Case ETL | `scripts/etl_golden_cases.py` |
| 实现状态 | `docs/260630_实现状态.md` |

## 七、知识检查

```bash
# knowledge MCP Server 健康状态
curl http://127.0.0.1:8106/health

# Neo4j 浏览器（手动检查图数据）
# http://localhost:7474  user: neo4j / pass: neo4jneo4j

# Capability Registry 列出所有 Agent
curl http://127.0.0.1:8021/a2a/v1/agents
```

## 八、关键环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY` | — | Embedding / LLM |
| `OPENAI_BASE_URL` | — | API 地址 |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 向量模型 |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | 图数据库 |
| `MILVUS_HOST` | `127.0.0.1` | 向量数据库 |
| `REPORTER_MODE` | `deep_agent` | 8D 生成模式 |
