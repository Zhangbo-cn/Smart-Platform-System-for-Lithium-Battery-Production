# MCP 工具服务层（独立进程，全平台共享）

一数据源一 Server；**业务 Agent 不内置 MCP 实现**，仅通过 `harness-core` 连接。

| Server | 端口 | 模块 |
|--------|------|------|
| mes | 8101 | `mes_server.mes_server` |
| scada | 8102 | `scada_server.scada_server` |
| erp | 8103 | `erp_server.erp_server` |
| lims | 8104 | `lims_server.lims_server` |
| qms | 8105 | `qms_server.qms_server` |

## 本地启动

```powershell
cd services/mcp
pip install mcp fastapi  # 或复用 rca-agent 环境
python -m mes_server.mes_server
# 其余同理；或 deploy/docker-compose.platform.yml 一键起
```

Agent 通过环境变量连接（默认 `http://localhost:810x/sse`）。
