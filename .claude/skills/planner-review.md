# Planner Review — ReAct 循环安全 + Tool Schema + 降级路径

审查 Planner Agent (`services/planner-agent/`) 的 ReAct 循环、tool 调用安全和模式降级。

## 审查维度

### 1. ReAct 循环安全
- `max_react_turns`（当前 8）硬限制是否正确执行——每轮是否递增计数器
- Thought→Action→Observation 循环——LLM 返回的 Action 是否经过 tool schema 校验
- 无 tool_call 时的处理——LLM 返回纯文本时是否正确终止循环（非 tool 调用 = Final Answer）
- Token 消耗追踪——每轮 LLM 调用是否记录 token 用量
- 循环超时保护——除 `max_react_turns` 外是否有总超时保护

### 2. Tool Schema 与参数校验
- `list_playbooks` / `get_capability_card` / `submit_plan` 三个 tool 的 schema 是否正确
- LLM 生成的 tool 参数是否经过 Pydantic 校验——缺失必填字段时的行为
- `submit_plan` 的输入验证——playbook 名称是否在 `list_playbooks` 返回的集合内
- 参数注入风险——用户输入是否可以操控 tool 参数（如 `batch_id` 从用户消息提取）

### 3. 模式切换
- React → Rule 降级条件——`llm_base_url` 未配置或 API 调用失败时是否正确切换
- 降级后的功能完整性——Rule 模式是否覆盖所有必需场景
- 模式切换的日志记录——降级事件是否打出 `logger.warning("planner.mode_fallback", ...)`

### 4. Prompt 设计
- System prompt 是否明确约束了输出格式（必须包含 playbook + params）
- Few-shot 示例是否与实际的 playbook 列表一致
- Prompt 注入风险——用户消息中是否可能包含对抗性指令
- 版本号管理——Prompt 变更是否有版本记录

### 5. Rule 引擎（plan_engine.py）
- 正则/关键词匹配的覆盖度——常见表达是否能匹配到正确的 playbook
- 置信度赋值——有 `batch_id` 时 0.75 / 无 `batch_id` 时 0.6，这两个值的依据
- 未知意图的处理——无法匹配任何 playbook 时的 fallback

### 6. HTTP 客户端
- `httpx.AsyncClient` 是否复用——当前每次 `_chat_completion` 新建 client（已知问题）
- 超时设置——60s HTTP 超时是否合理
- 重试——LLM API 调用是否有重试逻辑

### 7. 与 Orchestrator 的契约
- `PlanResult` 结构是否与 Orchestrator 期望一致——字段名、类型、置信度范围
- `submit_plan` 返回的 playbook 名称是否在 Orchestrator 的 playbook 注册表中

## 输出格式

```
[Critical|Warning|Suggestion] <file>:<line> — 描述
  原因: ...
  修复: ...
```

审查完成后输出摘要。
