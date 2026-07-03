# services 目录说明

| 目录 | 文档称呼 | 端口 | LLM |
|------|----------|------|:---:|
| `client-gateway/` | Client Gateway | 8010 | ❌ |
| `planner/` | Planner（Agent） | 8011 | ✅ |
| `orchestrator/` | Playbook Orchestrator | 8020 | ❌ |
| `capability-registry/` | Capability Registry | 8021 | ❌ |
| `a2a_server/trace_worker/` | Trace Worker | 8002 | ❌ |
| `a2a_server/report-agent/` | 8D Worker | 8004 | ❌ |
| `a2a_server/rca_agent/` | RCA Agent（junction） | 8003 | ✅ |
| `mcp/` | MCP 工具服务 | 8101–8105 | ❌ |

术语见 [docs/TERMINOLOGY.md](../docs/TERMINOLOGY.md)。

## 协作竖切

```
Client Gateway → Planner? → Orchestrator
  → triage_stub? → trace-worker → quality-rca-agent → report-8d-worker?
```

## 开发依赖

```powershell
pip install -e packages/platform-contracts -e packages/harness-core
.\scripts\link-rca.ps1
```
