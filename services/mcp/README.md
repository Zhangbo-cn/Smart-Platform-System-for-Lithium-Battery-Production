# MCP 工具服务层（独立进程，全平台共享）

一数据源一 Server；**业务 Agent 不内置 MCP 实现**，仅通过 `harness-core` 连接。

| Server | 端口 | 模块 | 数据源 |
|--------|:----:|------|--------|
| mes | 8101 | `mes_server.mes_server` | 生产执行 |
| scada | 8102 | `scada_server.scada_server` | 设备监控 |
| erp | 8103 | `erp_server.erp_server` | 企业资源 |
| lims | 8104 | `lims_server.lims_server` | 实验室信息 |
| qms | 8105 | `qms_server.qms_server` | 质量系统 |
| knowledge | 8106 | `knowledge_server.app` | 混合检索(Neo4j+Milvus) |
| eam | 8107 | `eam_server.eam_server` | 设备资产 |
| wms | 8108 | `wms_server.wms_server` | 仓储管理 |
| plc | 8110 | `plc_server.plc_server` | 产线控制 |

## 本地启动

```powershell
cd services/mcp
pip install mcp fastapi  # 或复用 rca-agent 环境
python -m mes_server.mes_server
# 其余同理；或 deploy/docker-compose.platform.yml 一键起
```

Agent 通过环境变量连接（默认 `http://localhost:810x/sse`）。
