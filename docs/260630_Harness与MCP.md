# Harness、MCP 与 RCA 整合定稿

> **版本**：v1.0 | 2026-06-29  
> **读者**：平台实施前架构对齐  
> **关联**：[docs/README.md](./260630_README.md) · [deploy/README.md](../deploy/README.md)

---

## 1. 总原则（先记住四句）

| # | 原则 |
|---|------|
| 1 | **Harness 是共享横切库**，不是某个 Agent 私有；所有 MCP 调用经 **ToolRegistry**（权限+审计+重试）。 |
| 2 | **MCP Server 按数据源建设**（一系统一 Server），**Agent 只声明用哪些**（AgentCard.`mcp_servers`），不各自造连接层。 |
| 3 | **记忆分三层域**：PlatformContext（跨 Agent）≠ Agent 会话记忆（Harness）≠ LangGraph checkpoint（RCA 内部）。 |
| 4 | **角色与权限分两层**：平台 JWT 角色管「谁能调 Router」；Tool ACL 管「谁能调哪个 MCP Tool」。 |

---

## 2. Harness 怎么做？

### 2.1 定位

**两层 harness，不要混读：**

| 层 | 位置 | 内容 |
|----|------|------|
| **平台横切** | `packages/harness-core/` | ToolRegistry、RBAC、audit、retry、MCP bootstrap、Session/EventBus |
| **RCA 领域** | `services/a2a_server/rca-agent/harness/` | hitl、memory、validation、eval、`checkpoint.py`（LangGraph）、本地 `circuit_breaker` |

文档写「横切已迁出」= **audit / permission / 压缩 / retry 等不再留在 RCA 的 `harness/` 里**；**不是**说 RCA 不该有 `harness/` 目录。领域 harness 故意保留在 RCA 进程内。

```
业务 Agent 进程
    │
    ├─ LangGraph / 规则 / HITL     ← 各 Agent 自己的「脑」（RCA：agent/ + harness/hitl 等）
    │
    └─ harness-core（共享库）        ← 手脚的「安全带」
           ├─ ToolRegistry         ← MCP 统一入口
           ├─ permission (RBAC)
           ├─ audit (trace_id)
           ├─ resilience (retry)
           ├─ context (压缩 Tool 输出)
           └─ …

RCA 另保留 harness/（非 re-export）：
           ├─ memory (STM / Working / Long-term)
           ├─ validation (FMEA)
           ├─ hitl (Broker)
           ├─ eval (Golden Set)
           ├─ checkpoint (LangGraph Saver)
           └─ circuit_breaker（本地）
```

**现状**：横切实现以 **`packages/harness-core/`** 为准；RCA 经 `pip install harness-core` 直引，领域模块仍在 `services/a2a_server/rca-agent/harness/`。

**目标形态**：

```
packages/harness-core/          # ✅ 已落地
services/a2a_server/rca-agent/             # 直引 harness_core；FMEA/HITL/checkpoint 仍放 RCA harness/
services/trace-agent/           # P1：引用 harness-core，bootstrap 子集
services/router/                # 仅用 audit + trace_id
```

若未来多个 Agent 共用 LangGraph checkpoint 装配，可再上提到 harness-core 或独立 `langgraph-harness` 包；当前仅 RCA 使用，保留在 RCA `harness/checkpoint.py`。

### 2.2 调用链（所有 Agent 统一）

```
Agent 节点 / Executor
    → ToolRegistry.invoke(name, args, user_id, user_role)
        → PermissionChecker.check_tool()
        → AuditTracer.span()
        → with_retry → MCPClient.call_tool()
            → MCP Server (mes/scada/…) → L3 数据库
```

**禁止**：Agent 代码里 `httpx.get(mes_db)` 或各服务各写一套 MCP 客户端且无审计。

### 2.3 与 Router / PlatformContext 的关系

| 组件 | 归属 | 内容 |
|------|------|------|
| **PlatformContext** | 平台 Router + Session Store | `batch_id`, `prior_evidence`, `rca.*` — **跨 Agent 黑板** |
| **MemoryHarness** | 单 Agent 进程内 | 用户偏好、会话摘要、案例向量 — **不**默认全量同步到 Context |
| **LangGraph Checkpointer** | RCA 服务内 | `QualityAnalysisState`, HITL `thread_id` — **不跨服务** |

Router **只读写 PlatformContext**；不把 RCA 内部 Graph state 暴露给其他 Agent。

---

## 3. 记忆、安全、工具、权限、角色 — 如何提前约束？

### 3.1 记忆

| 层级 | 存储 | 写入方 | 读取方 | 约束 |
|------|------|--------|--------|------|
| PlatformContext | Redis/PG（平台） | Router | 全部业务 Agent | **仅摘要字段**；禁止 dump MCP 全量 |
| STM | Redis | 各 Agent Harness | 同 Agent 内 Planner | `session_id` 隔离 |
| Working | PG | 各 Agent | 同 Agent | 用户偏好、未结案问题 |
| Long-term | Milvus+Neo4j | RCA 为主 | RCA、knowledge 检索 | Golden Case / FMEA 图 |
| RCA checkpoint | LangGraph Saver | RCA | RCA HITL | `rca.thread_id` 映射到 A2A Task |

**你的 Agent（RCA/8D）**：RCA 继续用现有 MemoryHarness；8D 初期 **只读 Context**，可不建长期记忆。

**壳 Agent（trace/triage）**：Phase 1 **无 Harness 记忆**，只返回 DTO；P1 实装时再 `pip install harness-core` + 按 AgentCard 引导 MCP。

### 3.2 安全与权限

| 机制 | 约束对象 | 实现位置 | 规则示例 |
|------|----------|----------|----------|
| **JWT + role** | 调平台 API 的人 | Client/Router/各 Agent API | `quality_engineer`, `quality_manager` |
| **AgentCard.scopes** | Router 委派目标 | Registry | RCA 不可 delegate `plc` |
| **Tool ACL** | MCP Tool | ToolRegistry | `erp.query_recipe` 仅 `quality_manager` |
| **sensitive 标记** | 高危 Tool | ToolSpec | 额外审计日志 |
| **Safety 独占** | `plc.write_*` | 仅 safety-agent 进程 bootstrap plc MCP | RCA/8D **禁止**注册 plc 写 Tool |
| **数据域** | 行级 | `check_data_scope(plant,line)` | Context 带 `factory_id` |

**提前约束文件**（建议后续落地）：

- `packages/platform-contracts/roles.py` — 角色枚举
- `packages/harness-core/tool_policies.yaml` — Tool→role 矩阵（从 RCA `bootstrap.py` 迁出）

### 3.3 工具调用（MCP）

- **注册**：进程启动时 `bootstrap_registry(registry)`，只连接 **本 Agent AgentCard.mcp_servers** 列表。
- **命名**：`{server}.{tool_name}`，如 `mes.get_batch_defects`。
- **发现**：Planner/Executor 用 `registry.list_tools(role)` 过滤后的列表，避免越权工具进入 Prompt。

### 3.4 角色定位（谁干什么）

| 角色 | 平台层 | Agent 层 |
|------|--------|----------|
| 巡检员 | 调 Client，`playbook=shift_patrol` | 不直连 RCA |
| 质量工程师 | `investigate` / `rca` | RCA HITL L1 |
| 质量经理 | 审批对外 8D / 停线 | HITL L2、Safety 审批 |
| 集团 IT | 维护 MCP/Registry | 无业务推理 |

Agent **服务身份**（服务间）用 **mTLS 或 Service JWT**，与**人工用户 JWT** 分开（P1 可先同一 secret，Claims 区分 `sub_type=agent`）。

---

## 4. MCP：谁造？谁用？

### 4.1 建设责任

| 责任 | 谁 | 产出 |
|------|-----|------|
| **造 MCP Server** | **平台** | `services/mcp/` 独立进程 |
| **造 Agent** | 各域 owner | 只写业务逻辑 + `bootstrap` 声明 |
| **造 Harness** | 平台 | `ToolRegistry` 统一封装 |
| **造 L3 系统** | MES/QMS 厂商 | MCP 适配真实 API |

**不是**「谁用到谁造整个 MCP 协议栈」，而是：**平台统一造 Server**；Agent 通过 AgentCard **声明依赖** + 进程内 **bootstrap 子集**。

### 4.2 MCP Server 清单与阶段

| Server | L3 | 建设状态（姊妹仓） | 负责优先级 |
|--------|-----|-------------------|------------|
| mes | MES | ✅ 有 | 你 + 追溯壳 |
| scada | SCADA | ✅ 有 | 你 + 质量预测（未来） |
| erp | ERP | ✅ 有 | 你（RCA 取证） |
| lims | LIMS | ✅ 有 | 你（RCA 取证） |
| knowledge | Milvus+Neo4j | ⚠️ RCA 内 FMEA 直连，MCP 未接 | 你 P1 可封装 `search_fmea` Tool |
| qms | QMS | ❌ Spec | 你（8D 写回）P1 |
| wms | WMS | ❌ | 仓储 owner |
| eam | EAM | ❌ | 设备 owner |
| plc | PLC/OT | ❌ | Safety owner **独占写** |

**部署**：唯一 Compose → `deploy/docker-compose.platform.yml`（详见 [deploy/README.md](../deploy/README.md)）

---

## 5. 各 Agent 用哪些 MCP？（明确矩阵）

### 5.1 你负责的 Agent（L0 + L1 壳）

| Agent | mcp_servers | bootstrap 逻辑 | 典型 Tools | 实现阶段 |
|-------|-------------|----------------|------------|----------|
| **quality-rca-agent** | mes, scada, erp, lims, knowledge | 姊妹仓 `bootstrap.py` 已接 4 域；knowledge 走 FMEA Registry | 批次缺陷、时序、物料、化验、FMEA 检索 | **P0 已有** |
| **report-reporter-agent** | qms, knowledge | P1：`qms.create_capa`, `knowledge.search_sop` | 8D 写回、SOP 引用 | 壳无 MCP；P1 接 qms |
| **trace-agent**（壳→实） | mes, scada, erp, lims | 与 RCA **子集重叠**；优先 mes+scada | `trace_batch`, 工艺参数时序 | L1 壳无；P1 复用 harness-core |
| **triage-agent**（壳→实） | mes | 仅告警/缺陷分类 | `get_alarm_feed`, `get_batch_defects` | L1 壳无；P2 轻量 |

**RCA 与 trace 的 MCP 关系**：

- **不要** trace 把全量 Tool 结果塞进 PlatformContext（只塞 **摘要 + evidence 列表**）。
- trace 实装后，RCA 收到 `prior_evidence` 应 **跳过** 已覆盖的 MCP 步骤（Executor 去重逻辑在 RCA 内）。

### 5.2 其他 Agent（仅登记或他人 owner）

| Agent | mcp_servers | 与红框关系 |
|-------|-------------|------------|
| quality-prediction | mes, scada, lims | 告警后 Router delegate RCA，**无 Context 字段** |
| patrol | mes, scada | 不喂 RCA |
| process-optimization | mes, scada, knowledge | coating 并行，不喂 RCA |
| equipment-health | scada, eam | pm_alert，不喂 RCA |
| wms-supply | wms, erp | 物料，不喂 RCA |
| **safety-agent** | **plc**, mes, qms | **不经 Harness 写 plc**；与 RCA 无数据交接 |

### 5.3 当前整合状态（2026-06）

| 项 | 状态 |
|----|------|
| `packages/harness-core/` | ✅ 已抽取 |
| `services/mcp/` | ✅ 权威位置；rca-agent 内已删 |
| `services/router/` + `registry/` | ✅ 最小版 |
| `deploy/docker-compose.platform.yml` | ✅ infra + profile `mcp` |
| RCA junction | ✅ `scripts/link-rca.ps1` |
| trace 实装 MCP | P1 |
| git 合并单仓 | 可选 |

---

## 6. RCA 与平台边界

- RCA 代码：`services/a2a_server/rca-agent/`（junction → 姊妹仓）
- Router **HTTP** 调 RCA，不 import RCA 模块
- 共享契约：`packages/platform-contracts`
- 部署：**仅** `deploy/docker-compose.platform.yml`

---

## 7. 检查清单

- [x] MCP 四域：`docker compose -f deploy/docker-compose.platform.yml --profile mcp up -d`
- [x] harness-core 独立包
- [x] RCA `prior_evidence` / A2A / trace_id
- [ ] Router Context → Redis
- [ ] trace/triage 实装 MCP（当前 stub）
- [ ] plc 写 Tool 未进 RCA bootstrap

---

## 8. 相关文档

[docs/README.md](./260630_README.md) · [deploy/README.md](../deploy/README.md) · [260630_服务架构.md](./260630_服务架构.md) · [260630_实现状态.md](./260630_实现状态.md)
