# RCA Agent Review — LangGraph 状态图 + FMEA 规则引擎 + HITL 安全

审查 RCA Agent (`services/a2a_server/rca-agent/`) 的图结构、根因推理正确性、HITL 断点安全。

## 审查维度

### 1. StateGraph 结构
- 所有节点的路由条件是否覆盖所有分支——`add_conditional_edges` 的路径映射无悬挂
- 节点名称与 `TypedDict` state key 是否一致——拼写错误会导致静默丢失数据
- `recursion_limit` 是否合理——当前 50，Reflector ≤3 轮 × 5 节点 = 15 步，50 是安全天花板
- 图的入口/出口是否正确——`set_entry_point()` / `set_finish_point()` 是否正确

### 2. FMEA 规则引擎
- `evidence_coverage × causal_chain_completeness` 计算是否正确——检查乘法逻辑、边界值（0、1、None）
- FMEA CSV 加载——空文件、格式错误、编码问题时的降级行为
- 规则命中/未命中分支——FMEA 无匹配时是否正确触发 `DEGRADE` 模式（纯 LLM + 低置信度 + HITL）
- 置信度阈值（0.7）——与 AgentCard `hitl_required_below` 字段是否一致

### 3. HITL 断点安全
- `interrupt()` 调用位置是否正确——必须在 Reflector 判定低置信度之后、Reporter 之前
- checkpoint 保存——确认 `interrupt()` 前的状态已持久化（Redis/MemorySaver）
- `Command(resume=feedback)` 恢复——用户签核数据是否正确传递到后续节点
- 超时处理——HITL 挂起时间过长时是否有超时取消逻辑
- 并行安全——同一 session 被多次 resume 时的幂等性

### 4. Executor 并行取证
- `asyncio.gather(*tasks, return_exceptions=True)` ——单个 MCP 失败是否导致整个 gather 失败
- FMEA 树节点裁剪逻辑——无关节点是否被正确过滤（不过滤会浪费 MCP 调用）
- MCP 调用超时——每个 MCP call 是否有独立的超时保护
- 结果合并——多个并行 MCP 返回结果的合并逻辑是否正确

### 5. Reflector 策略引擎
- DEEPEN / CORRELATE / REPLAN 三种策略的选择条件是否正确
- REPLAN 路径——新计划是否与旧计划有实质性差异（否则死循环）
- 最大重试 3 轮——第 4 轮是否正确触发 DEGRADE
- `_llm_correlation` / `_llm_replan` / `_llm_only_fallback` 三处 LLM 调用——
  difficulty=COMPLEX / sensitivity=MEDIUM 标注是否正确

### 6. Reporter 节点（RCA 内部）
- 产出 `rca_artifacts` 的结构是否与下游 Reporter Agent 的期望一致
- `root_cause` 是否被正确锁定——Reporter 不应改写此字段
- 与完整 8D Reporter 的职责边界——RCA Reporter 只出草稿，完整 8D 留给下游

### 7. 降级路径
- RCA graph 整体失败时 Orchestrator 的兜底路径是否正确触发
- 降级后的低置信度标记是否正确传递

## 输出格式

```
[Critical|Warning|Suggestion] <file>:<line> — 描述
  原因: ...
  修复: ...
```

审查完成后输出摘要。
