# 平台架构总览

## 服务一览

| 服务 | 端口 | 类型 | 引擎 | 职责 |
|------|:----:|------|------|------|
| Client Gateway | 8010 | 入口 | 无 LLM | 用户 HTTP 入口、SSE 推送 |
| Planner Agent | 8011 | Agent | ReAct LLM + 规则 | 理解用户意图，选 playbook |
| Orchestrator | 8020 | 工作流引擎 | 无 LLM | 按 YAML 剧本顺序调 Agent |
| Triage Agent | 8001 | Agent | LLM + 规则双模 | 异常分诊，识别 defect_type |
| Trace Worker | 8002 | Worker | 无 LLM | MCP 取证，纯数据查询 |
| RCA Agent | 8003 | Agent | LangGraph 有环图 | 跨 MCP 取证，锁定根因 |
| Reporter Agent | 8004 | Agent | Deep Agent | 8D 报告生成，QMS 写回 |
| Cap Registry | 8021 | 基础设施 | 无 LLM | 服务注册与健康检查 |

## 调用链

```
用户 ──HTTP──→ Gateway(8010) ──A2A──→ Planner(8011) 选剧本
                                          │
                                     Orchestrator(8020)
                                          │
                              Orchestrator 依次调：
                              Triage(8001) → Trace(8002) → RCA(8003) → Reporter(8004)
```

Orchestrator 不是 Agent，它只是用 A2AClient 发 HTTP 请求。每次 A2A 调用 = JSON-RPC over HTTP POST。

## 数据传递

Agent 不直连，不共享状态。Orchestrator 通过 PlatformContext 搬运数据：

```
RCA 返回 {root_cause: "..."}
  → Orchestrator 写入 ctx.rca.root_cause
  → 下一步读 ctx.rca.root_cause，构造 Reporter 请求
  → Reporter 收到 {root_cause: ctx.rca.root_cause, ...}
```

Agent 不知道自己在流水线里，它只知道自己收到请求→返回响应。

## 流程控制

流程由 playbooks.yaml 定义，每步有 condition 条件，运行时判断是否执行：

```yaml
- step: trace
  agent: trace-worker
  condition: "batch_id"          # 无 batch_id 就跳过
```

| 输入 | Triage | Trace | RCA | Reporter |
|------|:------:|:-----:|:---:|:--------:|
| "查B001批次" | ✅ | ✅ | ❌ | ❌ |
| "分析原因" | ✅ | ❌ | ✅ | ❌ |
| "出8D报告" | ✅ | ❌ | ✅ | ✅ |

## 异步与 SSE

调用模式：

```
POST /v1/assistant/tasks → 202 + sse_url（不等执行）
                         → 后台 Orchestrator 执行
                         → 每步完成推 SSE
                         → 用户连 SSE 看进度
```

## Trace Worker 不是 Agent

Worker 代码只有调 MCP 接口→拼数据→返回，没有 LLM 调用。

## 为什么 Orchestrator 不用 LangGraph

Orchestrator 做确定性编排，不需要推理。LangGraph 留给需要动态决策的地方（RCA Agent 内部）。
