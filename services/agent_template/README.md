# Agent 标准化模板

所有新增 Agent 继承此模板，统一 A2A + MCP + Registry 接入。

## 快速创建一个新 Agent

1. 复制 `app_template.py` 到 `services/<agent-name>/app.py`
2. 修改 `AgentConfig` 中的名称、端口、描述、capabilities
3. 实现 `_execute()` 方法（业务逻辑）
4. 在 `deploy/docker-compose.platform.yml` 中添加容器

## 模板提供的功能

| 功能 | 说明 |
|------|------|
| A2A 端点 | `tasks/send`, `tasks/resume`, `agent.json` |
| MCP 连接 | bootstrap 自动连接配置的 MCP 服务器 |
| Registry 注册 | 启动时自动注册到 Capability Registry |
| 健康检查 | `/health` 端点 |
| 降级支持 | MCP 连接失败时 `degraded` 状态 |
