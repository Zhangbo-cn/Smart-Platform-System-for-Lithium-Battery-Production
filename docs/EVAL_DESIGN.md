# 锂电平台 Agent 评测体系设计 v1

> 参考: 腾讯 Agent & Skill 测评方案 / 阿里 Agent 评测方法论 / Deep Mode 设计
> 范围: Planner / Orchestrator / RCA(LangGraph) / Reporter(Deep Agents)

---

## 一、四类 Agent 的评测侧重

| Agent | 类型 | 内部引擎 | 评测核心风险 | 优先级 |
|-------|------|---------|-------------|:------:|
| **Planner** | 任务拆解 | ReAct 循环 | Playbook 选错 / 参数提取不准 | P0 |
| **Orchestrator** | 编排路由 | YAML + SmartRouter | 步骤跳转错误 / 上下文丢失 | P0 |
| **RCA** | 根因分析 | LangGraph 5 节点 | 根因误判 / 证据链断裂 / HITL 异常 | P0 |
| **Reporter** | 报告生成 | Deep Agents 父子 | D4 根因被改写 / 报告不完整 / QMS 失败 | P1 |

---

## 二、评分体系（三类评委）

### 2.1 确定性评分器（Rule Scorer）

能用代码判断的就不用模型：

```
Planner:
  - playbook 是否在 enum 内
  - 参数 batch_id / defect_type 格式是否正确
  - SKU / 产线等必填字段是否缺失

Orchestrator:
  - Playbook 步骤顺序是否与 YAML 定义一致
  - 步骤间 PlatformContext 数据是否完整传递
  - SSE 事件序列是否符合预期

RCA:
  - root_cause 非空
  - confidence > 0
  - evidence 数量 >= prior_evidence
  - 工具调用序列包含关键步骤（mes.query_batch_trace 等）
  - HITL 场景返回 requires_hitl=true + thread_id

Reporter:
  - D4 根因原文未改写（substring check）
  - report_md 长度 > min_chars
  - capa_id 非空（QMS 写入成功）
  - generation_mode 正确
```

### 2.2 模型评分器（LLM-as-Judge）

用固定版本 LLM 按 Rubric 打分：

```
RCA:
  - 根因与证据的逻辑一致性  (0-5)
  - 推理链路完整性（无跳步） (0-5)
  - 专业术语使用正确性      (0-5)

Reporter:
  - D5 纠正措施的可执行性    (0-5)
  - 报告结构完整性           (0-5)
  - 语句专业度与清晰度       (0-5)

Planner:
  - Playbook 选择合理性      (0-5)
  - 参数提取准确性           (0-5)

Orchestrator:
  - 路由决策合理性（SmartRouter 场景）(0-5)
```

### 2.3 人工评分器（Human）

```
用途:
  - 校准 LLM Judge（每批次抽 20 条，一致率 >= 85%）
  - 诊断通过率异常（0% 或 100% 时人工介入）
  - 争议样本终判
```

---

## 三、维度与指标

| 维度 | Planner | Orchestrator | RCA | Reporter |
|------|---------|-------------|-----|----------|
| **功能正确性** P0 | playbook 选对率 | 步骤完整率 | 根因命中率 | D4 锁定率 |
| **过程质量** P1 | 工具调用顺序 | 上下文完整率 | 补查效率 | 子 Agent 协作 |
| **效率成本** P1 | LLM 调用次数 | 总耗时 | evidence 量 | token 消耗 |
| **鲁棒性** P0 | 异常 playbook 降级 | Agent 不可达容错 | HITL 多层恢复 | LLM 降级到模板 |
| **稳定性** P0 | pass@5 | pass@5 | pass@5 | pass@5 |

---

## 四、用例集结构

```
eval-core/
  data/
    golden/
      planner_cases.json      # 10-20 例
      orchestrator_cases.json # 10-15 例
      rca_cases.json          # 15-25 例（当前 10 例需扩）
      reporter_cases.json     # 15-20 例（当前 15 例）
    regression/
      (从 golden 毕业的用例)
  scorers/
    rule_scorer.py            # 确定性评分器
    llm_judge.py              # LLM-as-Judge 评分器
  runner.py                   # 评测执行器
  reporter.py                 # 报告生成器
```

### 用例格式

```json
{
  "case_id": "RCA-010",
  "agent": "quality-rca-agent",
  "category": "core_logic",
  "defect_type": "coating_density_low",
  "input": {
    "user_query": "涂布面密度偏差±2%，连续5卷超阈值",
    "batch_id": "B20260630",
    "prior_evidence": []
  },
  "expected": {
    "root_cause_contains": ["刮刀", "涂布"],
    "min_confidence": 0.6,
    "min_evidence": 1,
    "requires_hitl": false
  },
  "rubric": {
    "reasoning_coherence": 4,
    "evidence_completeness": 4
  }
}
```

---

## 五、评分公式

```
单次执行得分:
  rule_score = sum(确定性检查通过项) / sum(确定性检查项) × 60
  llm_score  = llm_judge_avg / 5 × 30
  efficiency_bonus = max(0, 10 - (实际耗时/基准耗时 - 1) × 5)
  trial_score = rule_score + llm_score + efficiency_bonus

稳定性判定:
  pass^5 = (5 次中全部 trial_score >= 80)
  用例总分 = avg(trial_score)  if pass^5 else 0

门禁标准:
  P0 用例: pass^5 必须通过
  P1 用例: 单次 score >= 80
  全量通过率 >= 90%
```

---

## 六、基线管理

```
建立流程:
  ① 设计用例 (input + expected 规则)
  ② 执行 1 次 → 人工确认结果可接受
  ③ 确认后固定该次 trace 为基线
  ④ 每次回归对比: 当前 trace vs 基线 trace

更新时机:
  - Agent 逻辑变更 → 重新确认基线
  - 模型版本升级 → 重新确认基线
  - 新增用例 → 执行确认后固定基线

基线内容:
  - 工具调用序列 (工具名 + 参数 + 顺序)
  - 最终输出 (root_cause / report_md/ playbook)
  - 耗时 / token 消耗
```

---

## 七、Badcase → 优化闭环

```
离线评测失败 / 线上 Bad Case
         ↓
  根因定位 (RCA for Eval):
    - 规则定位硬错误 (工具未调、参数错误)
    - Trace 对比定位过程偏差
    - LLM 辅助语义归因
         ↓
  修复建议:
    - Prompt 修改
    - 工具参数调整
    - LangGraph 节点逻辑调整
    - 降级策略补充
         ↓
  修复后:
    - 该 case 加入回归集
    - 跑全量验证无回归
```
