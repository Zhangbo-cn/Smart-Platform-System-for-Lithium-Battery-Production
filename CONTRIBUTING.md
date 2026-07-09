# 贡献指南

## 项目结构

```
packages/
  platform-contracts/   — 跨服务契约（AgentCard, A2A, ToolMeta, SmartRouter）
  harness-core/         — 共享 Harness（ToolRegistry, DAGEngine, AuditTracer, MCP Client）
  eval-core/            — 评估框架（LLM-as-Judge, 规则评分器）

services/
  orchestrator/         — Playbook 编排引擎
  client-gateway/       — 用户门户（REST + SSE）
  capability-registry/  — 服务注册与发现
  planner-agent/        — 意图识别（ReAct + 规则双模）
  a2a_server/
    rca-agent/          — 根因分析（LangGraph + FMEA）
    report-agent/       — 8D 报告生成（Deep Agents）
    triage-agent/       — 异常分诊
    trace_worker/       — 批次追溯
    patrol-agent/       — 开班巡线
    safety-agent/       — 安全控制（PLC 门闩）
    ...                 — 其他领域 Agent

  mcp/
    mes_server/         — 生产执行系统数据
    scada_server/       — 设备监控数据
    knowledge_server/   — 混合检索（Neo4j + Milvus）
    ...                 — 其他数据源
```

## 开发环境

```bash
# 安装 packages
pip install -e packages/platform-contracts
pip install -e packages/harness-core

# 运行全部测试
make test-all

# 语法检查
make lint
```

## 添加新 Agent

1. 复制 `services/agent_template/` 到 `services/<your-agent>/`
2. 实现 `_execute()` 方法
3. 在 `platform_contracts/agent_registry_seed.py` 注册 AgentCard
4. 在 `platform_contracts/mcp_tool_matrix.py` 配置工具权限
5. 配置 `docker-compose.platform.yml` 添加服务定义

## 添加新 MCP 数据源

1. 复制 `services/mcp/mes_server/` 创建新服务器
2. 实现 `@mcp.tool()` 函数
3. 在 `platform_contracts/mcp_tool_matrix.py` 注册工具
4. 配置 `docker-compose.platform.yml` 添加服务定义

## 测试要求

- 每个模块至少有一个测试文件
- MCP 服务器测试不需要真实数据库（mock 数据）
- conftest.py 配置好路径，保证可以从项目根目录运行
