# Architecture Guide — 锂电生产智能化平台

快速查找架构关键信息，替代翻阅 8+ 份文档。

## 一、服务映射

| 类别 | 服务 | 端口 | 部署名（Docker） | Profile |
|------|------|:----:|:----------------:|:-------:|
| 基础设施 | Redis | 6379 | redis | — |
| | PostgreSQL | 5432 | postgres | — |
| | Neo4j | 7474/7687 | neo4j | — |
| | Milvus | 19530 | milvus | — |
| 控制面 | Client Gateway | 8010 | client-gateway | — |
| | Planner Agent | 8011 | planner | — |
| | Playbook Orchestrator | 8020 | orchestrator | — |
| | Capability Registry | 8021 | capability-registry | — |
| 业务 | Trace Worker | 8002 | trace-worker | — |
| | RCA Agent (LangGraph) | 8003 | rca-agent | — |
| | Reporter Agent (Deep Agent) | 8004 | reporter-agent | — |
| MCP | MES | 8101 | mcp-mes | mcp |
| | SCADA | 8102 | mcp-scada | mcp |
| | ERP | 8103 | mcp-erp | mcp |
| | LIMS | 8104 | mcp-lims | mcp |
| | QMS | 8105 | mcp-qms | mcp |
| | **Knowledge (混合检索)** | **8106** | **mcp-knowledge** | **mcp** |

## 二、Agent 能力表

| Agent (服务名) | 类型 | 职责 | MCP 域 | 状态 |
|:--------------|:----:|------|--------|:----:|
| quality-rca-agent | Agent ✅ | 跨域取证 + FMEA 根因 + HITL | mes,scada,erp,lims,knowledge | 已落地 |
| report-reporter-agent | Worker | 8D 定稿 + QMS 写回 | qms,knowledge | 已落地 |
| trace-worker | Worker | MCP 批次追溯（无 LLM） | mes,scada,erp,lims | 已落地 |
| planner-agent | Agent (P1) | ReAct 任务拆解 | — | 已实现 |
| triage-stub | 规则引擎 | 异常分诊（内联 orchestrator） | mes | 已实现 |
| client-agent | Gateway | 统一入口 + SSE 推送 | — | 已实现 |
| safety-agent | Worker (规划) | 停线/改参门闩 | plc,mes,qms | P0 规划 |
| patrol/quality-pred/process-opt/equipment-health/wms-supply | 占位 | — | — | ⏸️ stub |

## 三、MCP 工具矩阵

| Server | 核心 Tool | 状态 |
|--------|-----------|:----:|
| mes | `query_batch_trace`, `query_defect_cells`, `get_process_params`, `get_shift_summary` | ✅ 4 |
| scada | `query_equipment_timeseries`, `detect_anomaly_window` | ✅ 2 |
| erp | `query_material_batch`, `query_recipe`(sensitive) | ✅ 2 |
| lims | `query_cell_test`, `batch_test_summary` | ✅ 2 |
| qms | `create_8d_draft`, `update_capa_status` | ✅ 2 |
| **knowledge** | `search_fmea`(Neo4j), `hybrid_search_golden_case`(Milvus+Neo4j), `search_sop` | ✅ 3 |

**安全**：`query_recipe` 需 `quality_manager/factory_director/group_it` 角色；`plc.*` 仅 Safety Agent 可调。

## 四、A2A 协议

- **端点**: `POST /a2a/v1/tasks/send` (业务 Agent)；`POST /a2a/v1/router/dispatch` (Orchestrator)
- **寻址**: AgentRouter 按 `capabilities` + AgentCard match
- **TaskState**: `SUBMITTED → RUNNING → (INPUT_REQUIRED) → COMPLETED/FAILED`
- **Payload**: JSON-RPC 风格，`{id, method: "tasks/send", params: {task: {id, session_id, message, metadata}}}`
- **通信规则**: 星型拓扑 — 业务 Agent 不直连，仅经 Orchestrator 委派

## 五、Playbook（剧本）

| Playbook | 步骤 | 适用场景 |
|----------|------|---------|
| `trace_only` | trace | 纯批次追溯 |
| `rca` | trace → rca | 已知异常需要根因 |
| `investigate` | triage? → trace → confirm? → rca | 未知问题深度分析 |
| `close_loop` | triage? → trace → rca(→HITL) → report_8d | 全链路闭环 |

配置：`config/playbooks.yaml`

## 六、RCA Agent 内部（LangGraph 5 节点）

```
Planner(LLM) → Executor(MCP Worker) → Reflector(FMEA规则+LLM)
       ⇄ 补查循环 (DEEPEN/CORRELATE/REPLAN/DEGRADE)
       → HITL(interrupt) → Reporter(LLM, 只改表述不改根因)
```

**置信度**（确定性公式，非 LLM 自评）：
```
confidence = evidence_strength × coverage
若命中链路 ≥ 2：× 0.85
HITL 阈值：0.7
```

**补查策略**：DEEPEN（下钻）/ CORRELATE（横向关联）/ REPLAN（重规划）/ DEGRADE（降级→HITL）/ CONFIRM（结论）

## 七、三层记忆

| 层级 | 存储 | 内容 | 范围 |
|------|------|------|------|
| STM | Redis | 单次 A2A Task 上下文 | 短期 |
| Working | PostgreSQL | 未结案工单、工艺记录 | 业务持久 |
| LTM | Neo4j + Milvus | FMEA 因果图 + Golden Case 向量 + SOP | 长期 |
| PlatformContext | Redis/PG | 跨 Agent 会话黑板 | session 级 |

## 八、Golden Case 混合检索

```
症状描述 → HybridRetriever
    ├── Milvus（语义向量） → cosine 相似 top-K
    └── Neo4j（FMEA 因果图）→ 共享根因路径 top-K
    → RRF 融合排序 → few-shot Markdown 注入 LLM
```

- 数据：`data/golden_cases.json`（15 条，12 缺陷类型，9 工序）
- ETL：`scripts/etl_golden_cases.py --rebuild`
- Tool：`knowledge.hybrid_search_golden_case` MCP

## 九、工程化

| 能力 | 实现 |
|------|------|
| 熔断器 | 3 次失败 → 断路 30s → 半开探测（`harness_core/resilience/circuit_breaker.py`） |
| 指数退避 | `harness_core/resilience/retry.py` |
| 降级路径 | 3 条（RCA 超时→兜底、Reporter 失败→模板、MCP 断流→空证据） |
| RBAC | 6 角色（`harness_core/permission/`） |
| 评测 | Golden Set 25 例 + 规则 Judge + LLM-as-Judge（`packages/eval-core/`） |
| Prompt 版本化 | YAML registry + Golden Set 绑定 + 回归测试 |
