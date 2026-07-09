# 锂电智能化平台 — 部署指南

> Compose 文件：`deploy/docker-compose.platform.yml`
> 基础镜像：根目录 `Dockerfile`

---

## 快速启动

```bash
# 1. 构建基础镜像（首次 / requirements 变更后）
docker build -t battery-agent-base .

# 2. 启动平台 + 全部 Agent（13 服务）
docker compose -f deploy/docker-compose.platform.yml up -d

# 3. 如需 MCP 服务器（9 个），加 --profile
docker compose -f deploy/docker-compose.platform.yml --profile mcp up -d

# 4. 查看状态
docker compose -f deploy/docker-compose.platform.yml ps
```

## 服务清单

| 类别 | 服务 | 端口 | Profile |
|------|------|:----:|:-------:|
| 基础设施 | redis | 6379 | — |
| | postgres | 5432 | — |
| | neo4j | 7474/7687 | — |
| | milvus | 19530 | — |
| 控制面 | capability-registry | 8021 | — |
| | planner | 8011 | — |
| | orchestrator | 8020 | — |
| | client-gateway | 8010 | — |
| 业务 Agent | triage-agent | 8001 | — |
| | trace-worker | 8002 | — |
| | rca-agent | 8003 | — |
| | reporter-agent | 8004 | — |
| | patrol-agent | 8005 | mcp |
| | safety-agent | 8099 | mcp |
| | quality-prediction | 8201 | mcp |
| | process-optimization | 8202 | mcp |
| | equipment-health | 8203 | mcp |
| | wms-supply | 8204 | mcp |
| MCP Server | mcp-mes | 8101 | mcp |
| | mcp-scada | 8102 | mcp |
| | mcp-erp | 8103 | mcp |
| | mcp-lims | 8104 | mcp |
| | mcp-qms | 8105 | mcp |
| | mcp-knowledge | 8106 | mcp |
| | mcp-eam | 8107 | mcp |
| | mcp-wms | 8108 | mcp |
| | mcp-plc | 8110 | mcp |

**总计**：13 服务（默认）+ 10 MCP（`--profile mcp`）= 23 容器

## 启动顺序

```
redis / postgres / neo4j / milvus       ← 基础设施
    ↓
capability-registry / planner           ← 控制面
    ↓
orchestrator                            ← 依赖 registry + redis
    ↓
trace-worker / rca-agent / reporter-agent / client-gateway / triage-agent
    ↓
patrol / safety / prediction / optimization / equipment / wms  ← 领域 Agent
```

## 环境变量

| 服务 | .env 位置 | 关键变量 |
|------|----------|---------|
| planner | `services/planner-agent/.env` | `LLM_API_KEY`, `LLM_BASE_URL` |
| reporter | `services/a2a_server/report-agent/.env` | `LLM_API_KEY`, `LLM_BASE_URL` |
| rca-agent | `services/a2a_server/rca-agent/.env` | `LLM_API_KEY`, `LLM_BASE_URL` |
| triage-agent | `services/a2a_server/triage-agent/.env` | `LLM_API_KEY`, `LLM_BASE_URL` |

## 竖切验证

```bash
# Gateway 直连
curl -X POST http://127.0.0.1:8010/v1/assistant/tasks \
  -H "Content-Type: application/json" \
  -d '{"message":"分析批次 B20250630 涂布虚焊","batch_id":"B20250630"}'

# Orchestrator 直连
curl -X POST http://127.0.0.1:8020/a2a/v1/router/dispatch \
  -H "Content-Type: application/json" \
  -d '{"playbook":"investigate","batch_id":"B001","message":"容量偏低","confirm_rca":true}'

# 健康检查
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8021/a2a/v1/agents
```

## 仅启动基础设施（开发时手动起 Agent）

```bash
docker compose -f deploy/docker-compose.platform.yml up -d redis postgres neo4j milvus milvus-etcd milvus-minio
# 然后手动 uvicorn 各 Agent
```

## 测试

```bash
# 安装后全量测试
make test-all

# 或逐模块测试
python -m pytest packages/harness-core/tests/
```
