# Code Review — Battery Agent Platform

审查本项目的 Python 代码，按以下维度逐一检查，输出分级发现（Critical / Warning / Suggestion）。

## 审查维度

### 1. 导入与模块结构
- 是否缺少 `from __future__ import annotations`（项目惯例，几乎所有文件都有）
- 是否使用绝对导入（项目规范，禁止相对导入如 `from . import foo`）
- 是否循环依赖 `__init__.py` 网关（`platform_contracts/__init__.py` 和 `harness_core/__init__.py` 是重导出网关，不应被内部模块反向依赖）
- 是否存在未使用的导入

### 2. 错误处理
- `except Exception:` 是否覆盖过宽——必须附带 `logger.exception()` 记录完整回溯，否则 Critical
- `except Exception:` + `noqa: BLE001` 需标注理由（当前 a2a.py:149/175、react_agent.py:133）
- 降级链是否正确闭合——检查 `deep_agent → template`、`LLM → rule`、`RCA → fallback` 三层降级分支是否都有覆盖
- 是否存在吞异常而不记录日志的路径（如空 `except: pass`）

### 3. 异步安全
- 模块级可变全局变量（如 `report_tools.py:12` 的 `_LOCKED`）——多请求并发下存在竞态条件，Warning
- `asyncio.gather` 是否正确使用 `return_exceptions=True`（Executor 正确使用了，其他位置需检查）
- `httpx.AsyncClient` 是否在每次调用时新建而非复用（planner-agent 已知问题：`react_agent.py:50`）
- `asyncio.create_task` 是否被正确 await 或取消，避免「fire and forget」泄漏

### 4. 类型安全
- `dict[str, Any]` 返回类型是否掩盖了结构化数据——建议用 TypedDict 或 Pydantic model 替代
- `# type: ignore` 是否应当修复而非忽略（当前 3 处：`mcp_tool_matrix.py:253`、`plan_engine.py:48/74`）
- `assert` 是否用于运行时保护（如 `mcp_client.py:27/38` 的 `assert self._session`）——用 `if ... is None: raise` 替代，`assert` 在 `-O` 模式下被跳过

### 5. 日志规范
- 是否使用 `structlog.get_logger(__name__)`（项目标准）
- 日志调用是否携带结构化上下文（`logger.info("event_name", key=value, ...)`）
- 审计相关是否使用具名 logger（`llm.usage`、`llm.route`）
- `logger.exception()` vs `logger.warning()` vs `logger.error()` 使用是否恰当：
  - `exception()`: 带完整回溯的异常
  - `warning()`: 降级/回退事件
  - `error()`: 非异常但需人工介入的错误

### 6. 配置安全
- 敏感值（API key、password）是否从环境变量/.env 读取，不硬编码
- `pydantic-settings` 的 `extra="ignore"` 是否导致拼写错误被静默忽略（检查字段名一致性）
- 默认值是否合理——空字符串 `""` 作为 `llm_base_url` 默认值是否在运行时被正确校验

### 7. 工具定义
- MCP 工具是否在 `mcp_tool_matrix.py` 的 `AGENT_ALLOWED_TOOLS` 中注册
- `exclusive_agent` 字段是否正确保护了安全关键工具（`plc.emergency_stop`、`plc.write_setpoint`）
- 工具 handler 是否包裹了 `@with_retry`（仅 MCP 网络瞬断应重试，业务逻辑错误不应重试）

### 8. A2A 协议
- 新增 Agent 是否实现了 `AsyncA2AServer.handle_task()` 方法
- AgentCard 是否在 `agent_registry_seed.py` 中注册
- `TaskState` 生命周期转换是否完整（SUBMITTED→RUNNING→INPUT_REQUIRED/COMPLETED/FAILED）
- HITL 路径：`interrupt()` → `INPUT_REQUIRED` → `tasks/resume` → `Command(resume=...)` 链路是否闭环

### 9. LangGraph 特定检查
- `StateGraph` 所有节点的路由条件是否覆盖所有分支（不能有悬挂的 conditional edge）
- `recursion_limit` 是否合理设置（Reporter deep agent: 25, RCA graph: 50）
- Checkpointer 是否正确配置（Redis MemorySaver 或 InMemory）
- `interrupt()` 之后是否有对应的 `Command(resume=...)` 恢复路径

### 10. 项目特定反模式
- **可变全局状态**：`report_tools._LOCKED`、任何模块级 `{}`/`[]`
- **测试中 `os.chdir()`**：`test_deep_agent.py:12-14`，应使用 `monkeypatch` 或 fixture
- **同步阻塞在 async 路径**：如 `time.sleep()` 在 async 函数中，应用 `asyncio.sleep`
- **重复的 `noqa` 注释**：应修复根因而非持续抑制 lint

## 输出格式

每个发现按以下格式输出：

```
[Critical|Warning|Suggestion] <file>:<line> — <一句话描述>
  原因: <为什么是问题>
  修复: <具体怎么改>
```

Critical = 运行时崩溃/安全漏洞/数据丢失风险
Warning  = 并发 bug/资源泄漏/可维护性问题
Suggestion = 风格/最佳实践偏差

审查完成后输出摘要：`N Critical, M Warning, K Suggestion`。
