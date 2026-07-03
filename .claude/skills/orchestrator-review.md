# Orchestrator Review — Playbook 路由 + A2A 委派 + PlatformContext 状态机

审查 Orchestrator (`services/orchestrator/`) 的调度逻辑、错误传播、状态一致性。

## 审查维度

### 1. Playbook 路由正确性
- `_run_playbook()` 中每个 playbook 分支是否完整覆盖——有无悬挂的 `if/elif` 末尾缺 `else` fallback
- `resolve_playbook()` 的输入验证——空 playbook、未知 playbook、参数缺失时的行为
- 剧本步骤之间的依赖关系是否正确——后续步骤依赖前序步骤的 `session_id`/`root_cause` 等字段，检查是否为 None
- Playbook YAML 定义与实际 if-else 分支是否一致

### 2. A2A 调用模式
- 每次 `A2AClient.send()` 前是否正确构造了 `session_id`（trace_id 贯穿全链路）
- `A2AClient.resume()` 路径——HITL 恢复时 `task_id`/`thread_id` 是否从 PlatformContext 正确读取
- A2A 调用失败后的降级逻辑——是否为每个下游 Agent 定义了 fallback（RCA 超时→兜底，Reporter 失败→不阻塞？）
- HTTP 超时设置是否合理（当前 120s，RCA 可能超长？）

### 3. PlatformContext 状态一致性
- 写入 Context 的字段是否与读取的字段名一致——`rca.root_cause`、`report_8d.capa_id` 等 key 是否有拼写错误
- 并发安全：多个步骤写入同一 Context 时有无竞态（Orchestrator 当前是单线程 async，但需确认）
- Context TTL（24h）是否合理——长时间 HITL 暂停后 Context 是否过期
- `prior_evidence` 是否正确在 Trace→RCA→Reporter 之间传递

### 4. 错误传播
- 下游 Agent 返回 `FAILED` 时 Orchestrator 如何处理——是否区分「可重试」和「致命」错误
- 部分步骤失败时后续步骤是否继续——如 Trace 成功但 RCA 失败，Reporter 是否还能跑（有兜底路径）
- `A2AError` 异常是否正确捕获并转为 PlatformContext 中的错误标记
- Orchestrator 自身的异常是否被顶层 handler 捕获——避免 500 裸奔

### 5. Session 管理
- `session_id` 生成规则——`sess_{uuid.hex[:12]}` 是否有碰撞风险
- 同一 session 的多次 A2A 调用是否共享 `trace_id`
- SSE 推送——TaskEvent 的生命周期是否完整（SUBMITTED→RUNNING→...→COMPLETED/FAILED）

### 6. 可观测性
- 每个步骤是否记录结构化日志（`logger.info("orchestrator.step", step=..., duration_ms=...)`）
- A2A 调用是否记录下游 Agent 的响应时间
- 全链路 trace_id 是否正确贯穿

## 输出格式

```
[Critical|Warning|Suggestion] <file>:<line> — 描述
  原因: ...
  修复: ...
```

审查完成后输出摘要。
