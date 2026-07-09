# 锂电生产智能化平台

> **A2A + MCP** 锂电制造智能分析层。  
> **文档**：[docs/README.md](docs/README.md) · **术语**：[docs/TERMINOLOGY.md](docs/TERMINOLOGY.md) · **部署**：[deploy/README.md](deploy/README.md)

## 快速开始

```powershell
.\scripts\link-rca.ps1
.\scripts\setup-dev.ps1
docker compose -f deploy/docker-compose.platform.yml --profile mcp up -d
# 另开终端起各服务 → 见 deploy/README.md
```

## 测试

```bash
# 安装
pip install -e packages/platform-contracts
pip install -e packages/harness-core

# 运行全部测试
make test-all
```

**279+ 测试**覆盖全部服务模块。

## 仓库结构

```
packages/
  platform-contracts/  AgentCard, A2A, ToolMeta, SmartRouter
  harness-core/        ToolRegistry, DAGEngine, AuditTracer, OTel
  eval-core/           评估框架

services/
  orchestrator/        Playbook 编排 + DAG 引擎
  client-gateway/      用户门户
  planner-agent/       LLM 意图识别
  capability-registry/ 服务注册与健康探活
  a2a_server/          领域 Agent（RCA, Report, Triage, Trace, Patrol...）
  mcp/                 9 个 MCP 服务器

deploy/                docker-compose.platform.yml
docs/       文档导航 · REQUIREMENT_TEMPLATE
scripts/    link-rca · setup-dev · start-mcp
```

## 架构一句话

Client Gateway → Planner? → Orchestrator → 业务能力服务（星型，无互连）→ harness-core → MCP；**RCA Agent** 已落地，多数能力服务为 Worker。

## 版本

- 微服务定稿：2026-06（见 [docs/SERVICE_ARCHITECTURE.md](docs/SERVICE_ARCHITECTURE.md)）
- 架构 Spec：2026-06
