# Context Memory Master — 上下文留痕 & 记忆沉淀

## 一句话

当上下文接近阈值（谈话超过 ~15 轮 / 检测到 compaction 即将发生），自动执行"留痕→沉淀→压缩"三阶段，让每次对话结束前都保存关键信息，下一轮能无缝接续。

## 三阶段流程

```
检测到 → ① 留痕（trace）：记录本次对话做了什么
触发点 → ② 沉淀（memory）：提取用户偏好 / 决策 / 反馈到 memory 文件
         → ③ 准备压缩：清理不再需要的上下文，为 compaction 做标记
```

---

## ① 留痕（Trace）——工作处处留痕

### 什么算"痕"

| 类型 | 例子 | 保存方式 |
|------|------|---------|
| 架构决策 | 选择 FastAPI 而非 Flask、保留 YAML fallback | `memory/` 下 `adr-*.md` |
| 文件变更 | 创建/修改了哪个服务的哪个文件 | `traces/YYYY-MM-DD.md` 追加 |
| 用户偏好 | 用户说"我不喜欢 xxx"、"优先用 yyy" | `memory/` 下 `preference-*.md` |
| 评测结果 | `run_eval.py` 的输出关键数字 | `metrics/YYYY-MM-DD.md` |
| Bug 原因 | 排查后确认的根因 | `memory/` 下 `bug-*.md` |
| 用户提问而未实现 | 用户提了需求但决定暂缓 | `memory/` 下 `backlog-*.md` |

### 自动留痕时机

每次以下操作完成后自动写一条 trace：

```yaml
写/改文件后:    "修改了 services/orchestrator/app.py → 添加 next_agents 路由"
评测运行后:     "RCA eval: accuracy 87.5% (14/16)"
架构决策后:     "决定保留 YAML 作为 deterministic fallback"
用户反馈后:     "用户要求 Reporter 输出纯文本不要 Markdown"
发现 Bug 后:    "Reporter Agent 在 input_empty 时返回 500，根因是 validate() 缺少空值检查"
```

### Trace 格式

```
traces/2026-07-03.md:
  ## 2026-07-03 会话留痕
  
  ### 14:30 — 修改 orchestrator/app.py
  - 变更: 添加 _discover_from_registry() 动态发现
  - 原因: 支持 Agent 动态注册后自动发现
  - 涉及文件: services/orchestrator/app.py (+45/-3)
  
  ### 15:10 — 架构决策
  - 决策: 保留 YAML playbook 作为 fallback
  - 理由: 制造场景须过 IATF 16949，确定性路径必须存在
  - 关联: [[a2a-refactoring-plan]]
  
  ### 15:45 — 用户反馈
  - 内容: "enum 的动态生成体现智能性"
  - 操作: 已记录到 memory/user-preference-dynamic-enum.md
```

---

## ② 沉淀（Memory）——沉淀用户记忆

### 提取规则

每轮对话结束时，扫描以下**信号**：

```
1. 用户明确表达了喜好 → 写 user 类型 memory
   "我不喜欢 xxx" / "优先用 yyy" / "zzz 保持不动"

2. 用户对某做法表示认可/批评 → 写 feedback 类型 memory
   "你这样做好" / "这个不对" / "我不理解为什么这样"

3. 达成了架构决策 → 写 reference 或 project 类型 memory
   "决定用 xxx 替代 yyy"
   "结论：不重构 zzz，保持现状"

4. 发现了重要的背景信息 → 写 reference 类型 memory
   "这个接口需要 xxx 角色权限"
   "这个 MCP Server 返回数据格式是 yyy"
```

### 每个 memory 的标准格式

```markdown
---
name: <kebab-case-slug>
description: <一句话摘要 — 用于召回时判断是否相关>
metadata:
  type: user | feedback | project | reference
---

具体内容...

**Why:** 当时为什么记录这个
**How to apply:** 后续使用时怎么做
**Related:** [[other-memory]] [[another-memory]]
```

### 自动更新 MEMORY.md

每次新建/更新 memory 时，同步更新 `memory/MEMORY.md`：

```
- [Title](file.md) — 一句话摘要
```

---

## ③ 压缩准备（Compaction Prep）

### 检测阈值

以下任一触发即进入压缩准备：

- 当前上下文已超过 **~80% 的估算容量**（手动感受：翻页超过 3 屏，或回顾时感觉前面内容遥远）
- 已经完成了至少 **1 个完整的"任务循环"**（创建/修改文件 → 验证 → 提交）
- 用户发出了 `/compact` 命令

### 压缩前检查清单

在执行 compaction 之前，确保以下内容都已持久化：

```
[x] 本次会话创建/修改了哪些文件 → traces 已记录
[x] 本次会话的架构决策 → memory 已记录
[x] 用户的新偏好/反馈 → memory 已记录
[x] 未完成的任务 → backlog 已记录
[x] 下次接续需要知道什么 → concise context note 已写入
```

### Context Note（接续上下文）

在压缩前写一个 `CONTEXT_NOTE.md` 到 session tmp 目录（或用 `/remember` 保存到记忆），格式：

```
---
name: session-continuation-context
description: 本次会话的关键上下文，压缩后接续用
metadata:
  type: reference
---

## 当前进度
- [x] Phase 1 传输层替换 — 已完成
- [ ] Phase 2 动态发现 — 进行中（50%）
  - agent_bootstrap.py 已添加 register_with_registry()
  - capability-registry 的 POST/register 端点已实现
  - 未完成：DiscoveryClient 集成

## 待决策项
1. AIAgentRouter 是否替换 YAML playbook — 等用户返回后讨论
2. Reporter 的 streaming 模式选 SSE 还是 WebSocket — 等用户确认

## 已知问题
- Knowledge 和 PLC MCP 端口冲突（都是 8106）
- planner-agent 每次 LLM 调用新建 httpx client（性能问题）
```

---

## 触发机制

### 自动触发（在后台执行）

| 触发点 | 动作 |
|--------|------|
| 写文件后 | 推一条 trace 到 `traces/YYYY-MM-DD.md` |
| 用户给出明确反馈时 | 提取 user/feedback memory |
| 每完成一个逻辑步骤（task done） | 扫描是否有决策需要记录 |
| `/compact` 前 | 执行完整的三阶段流程 |
| 上下文明显变长（~15 轮以上) | 主动建议 "是否需要做一次留痕？" |

### 手动触发

用户主动调用：
```
/留痕         → 执行留痕流程
/沉淀         → 执行记忆提取流程（会提问确认）
/compress     → 执行完整三阶段
/status       → 显示当前 memory 和 traces 概况
```

### 半自动模式（推荐）

```
1. /留痕 或检测到阈值 → 自动提取所有可确定的 trace
2. 对于不确定的部分 → 用 AskUserQuestion 确认
   "检测到您似乎偏好动态枚举而不是静态 enum，是否记录到 memory？"
3. 确认后写入 → 更新 MEMORY.md
```

---

## 目录结构

```
.claude/
  skills/
    context-memory-master.md    ← 本文件
memory/                          ← 记忆沉淀
  MEMORY.md                      ← 索引
  user-*.md                      ← 用户特征 / 偏好
  feedback-*.md                  ← 用户反馈
  project-*.md                   ← 项目约束 / 进展
  reference-*.md                 ← 参考信息
  adr-*.md                       ← 架构决策记录
traces/                          ← 工作留痕
  2026-07-03.md                  ← 每日 trace 日志（追加）
```

---

## 与本项目已有技能的交互

| 技能 | 交互方式 |
|------|---------|
| `dev-workflow.md` | 留痕时记录执行了哪些命令及其输出 |
| `architecture-guide.md` | 沉淀时如果涉及架构变更，更新此文件 |
| `code-review.md` | 审查结果自动留痕到 traces |
| `*review.md` | 每个 review skill 执行完自动触发一次留痕 |
| 现有 memory 文件 | 新建 memory 时检查是否和已有 memory 重复，避免冗余 |

---

## 禁忌

- **不要记录敏感信息**（API key、密码、生产数据）
- **不要在没有用户确认的情况下修改 memory 中的 user/feedback 类型**（project/reference 可以自动写）
- **不要过度留痕**——只记录有意义的变更，不记录每一行代码的修改
- **压缩前的 CONTEXT_NOTE 不超过 500 字**，过长就失去了"紧凑可读"的意义
- **不要用这个 skill 代替 git commit**——代码级别的变更用 git history，memory 只记录"为什么要改"
