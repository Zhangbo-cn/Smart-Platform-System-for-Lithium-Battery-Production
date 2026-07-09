# 变更日志

## [1.0.0] - 2026-07-09

### 新增
- DAGEngine：并行节点、条件分支、重试熔断、断点续跑、Mermaid 可视化
- ToolMeta Schema：LLM 输出结构化校验（参数类型、枚举、必填检查）
- AgentCard.agent_type：动态 Agent 分发（llm_agent/data_worker/router/planner）
- 三层记忆系统：STM 自动压缩 + Working 轮次记录 + LTM 向量案例
- OpenTelemetry 统一 tracing：替代三套分散的可观测系统
- GitHub Actions CI：自动语法检查 + 全模块测试
- Makefile：test/test-all/lint/clean 命令
- .env.example：全部 23 个服务目录补齐
- EditorConfig + ruff.toml + .gitattributes：代码风格统一

### 改进
- Orchestrator _call_step：从硬编码 if-elif 改为 agent_type 动态分发
- RCA LangGraph：新增内联 triage_node + gather_node（减少 2 次 A2A 调用）
- Planner：动态工具列表注入 prompt + LLM 输出 Schema 校验
- AuditTracer：从 structlog 改为 OTel span + error_type 5 分类
- Playbook YAML：从线性 steps 升级到 DAG nodes 格式
- 复合条件表达式：支持 "not A and not B" 完整解析

### 修复
- AgentHealthStatus.UP→OK（capability-registry 运行时崩溃）
- knowledge_server _ensure_clients 异常逃逸（加 try/except）
- datetime.utcnow() 弃用警告（5 个 MCP 文件）
- docker-compose knowledge_server 模块路径

### 测试
- 新增 70+ 测试用例，覆盖全部 18 服务 + 9 MCP
- 修复 conftest.py 路径，支持从项目根运行测试
- 总计 279+ 测试全部通过
