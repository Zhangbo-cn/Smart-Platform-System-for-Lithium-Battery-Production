# Eval & Golden Set — 评测体系操作手册

RCA + Reporter Agent 的离线评测系统。`python scripts/run_eval.py --all` 一键跑完 25 例。

## 一、评测 CLI

```bash
# 全部评测（RCA 10 例 + Reporter 15 例）
python scripts/run_eval.py --all

# 仅 Reporter 模板评测
python scripts/run_eval.py --reporter-template

# 仅 RCA 规则评测
python scripts/run_eval.py --rca-rule

# LLM-as-Judge 演示（无 API key 时优雅降级）
python scripts/run_eval.py --llm-judge

# Prompt 回归测试（对比新旧版本）
python scripts/run_eval.py --prompt-regression
```

## 二、Golden Set 结构

### Reporter（15 例）

`packages/eval-core/data/reporter_golden.json`

```json
{
  "cases": [
    {
      "case_id": "RPT-001",
      "root_cause": "涂布面密度不均 → 容量衰减",
      "defect_type": "capacity_fade",
      "min_report_chars": 200
    }
  ]
}
```

评测指标：
| 指标 | 阈值 | 含义 |
|------|------|------|
| `d4_locked` | =1.0 | D4 根因必须原文保留，不可改写 |
| `length_ok` | ≥200字符 | 报告完整性底线 |
| `score` | 加权 | 通过判定 |

### RCA（10 例）

`packages/eval-core/data/rca_golden.json`

```json
{
  "cases": [
    {
      "case_id": "RCA-001",
      "defect_type": "涂布面密度偏差",
      "root_cause": "浆料粘度异常→涂布面密度偏低",
      "expected_substrings": ["粘度", "面密度"],
      "min_evidence": 3
    }
  ]
}
```

评测指标：
| 指标 | 阈值 | 含义 |
|------|------|------|
| `root_cause_coverage` | — | 根因是否包含 expected_substrings |
| `evidence_sufficiency` | — | 证据条数 ≥ min_evidence |
| `fmea_hit_rate` | ≥0.80 | FMEA 规则命中率 |

## 三、Prompt 版本管理

配置：`packages/eval-core/prompts/registry.yaml`

```yaml
prompts:
  rca_planner:
    current_version: "1.0"
    versions:
      "1.0":
        file: "rca_planner_v1.0.md"
        golden_set: "rca_planner_golden.jsonl"
        metrics:
          plan_validity: ">=0.90"
  rca_reflector:
    current_version: "1.0"
    metrics:
      fmea_hit_rate: ">=0.80"
      confidence_calibration: ">=0.75"
  rca_reporter:
    ...
  reporter_main:
    ...
    metrics:
      d4_locked: "=1.0"
```

**修改 Prompt 的流程**：
1. 在 `registry.yaml` 加新版本 entry
2. 创建 prompt 文件（如 `rca_planner_v1.1.md`）
3. 运行 `python scripts/run_eval.py --prompt-regression` 对比新旧版本
4. 通过后更新 `current_version`

## 四、规则 Judge（0 token，每次提交可跑）

位于 `packages/eval-core/src/eval_core/judge.py`：

| Judge | 用途 | 断言内容 |
|-------|------|---------|
| `rule_judge_reporter_d4_locked` | Reporter D4 根因不变 | 根因原文是否在 report 中出现 |
| `rule_judge_rca` | RCA 根因覆盖度 | expected_substrings 是否都在 root_cause 中 |
| `llm_judge_consistency` | 语义一致性（可选） | 无 API key 时跳过，不阻塞 |

规则 Judge 是确定性断言——同一输入永远同一输出，0 token 成本。

## 五、添加新 Golden Set 测试用例

### Reporter 用例
1. 在 `reporter_golden.json` 的 `cases[]` 添加：
```json
{
  "case_id": "RPT-016",
  "root_cause": "注液量偏差→循环寿命衰减",
  "defect_type": "electrolyte_leakage",
  "min_report_chars": 250
}
```
2. 运行 `python scripts/run_eval.py --reporter-template` 确认通过

### RCA 用例
1. 在 `rca_golden.json` 的 `cases[]` 添加：
```json
{
  "case_id": "RCA-011",
  "defect_type": "涂布不均匀",
  "root_cause": "模头垫片磨损→唇口间隙不均→面密度偏差",
  "expected_substrings": ["模头", "垫片", "间隙"],
  "min_evidence": 2
}
```
2. 运行 `python scripts/run_eval.py --rca-rule` 确认通过

## 六、文件索引

| 文件 | 说明 |
|------|------|
| `scripts/run_eval.py` | CLI 入口 |
| `packages/eval-core/src/eval_core/judge.py` | 规则 Judge + LLM-as-Judge |
| `packages/eval-core/src/eval_core/reporter_eval.py` | Reporter 评测引擎 |
| `packages/eval-core/data/reporter_golden.json` | Reporter 15 例 |
| `packages/eval-core/data/rca_golden.json` | RCA 10 例 |
| `packages/eval-core/prompts/registry.yaml` | Prompt 版本注册表 |
| `packages/eval-core/prompts/*.md` | Prompt 模板文件 |
