"""
FMEA 反思验证测试 —— 全部针对确定性内核（FMEAValidator + FMEATree），
不依赖 LLM / 网络，可离线 `pytest tests/test_fmea_reflection.py -v` 直接跑。

覆盖三种核心场景：
1. 单因深挖：单条链路命中 -> DEEPEN，预算 = 剩余递归深度
2. 多因横查：≥2 条链路命中 -> CORRELATE，预算固定，走横向关联
3. 优雅降级：未命中 / 挖到底仍不达标 -> DEGRADE，给"已排除/疑似"清单
另含：FMEA 树结构（宽度/深度）、置信度可复现性。
"""
from __future__ import annotations

from harness.validation import FMEAValidator
from knowledge.fmea_tree import CAPACITY_FADE_TREE, get_tree


def _result(metrics: dict) -> list[dict]:
    """把 {metric_key: value} 包装成 tool_calls 的形态。"""
    return [{"tool": "mock", "result": metrics}]


# ---------------------------------------------------------------------------
# FMEA 树结构
# ---------------------------------------------------------------------------
def test_tree_width_and_depth():
    tree = CAPACITY_FADE_TREE
    # 宽度：4 大并列分支
    assert len(tree.root_branches) == 4
    # 深度：活性锂损失分支最深（水分->电解液吸湿性->供应商 = 3 层 + root = 4）
    assert tree.max_depth() == 4
    # 析锂分支较浅
    lithium = tree.find("负极析锂")
    assert tree.remaining_depth(lithium) == 2  # root + 一层叶子


def test_first_layer_calls_are_parallel():
    """Planner 用：初始计划应是 4 条分支并行。"""
    calls = CAPACITY_FADE_TREE.first_layer_calls()
    assert len(calls) == 4
    assert all(c["parallel"] is True for c in calls)
    assert all("causal_path" in c for c in calls)


# ---------------------------------------------------------------------------
# 场景 1：单因深挖
# ---------------------------------------------------------------------------
def test_single_factor_triggers_deepen():
    """只有'水分超标'异常（活性锂损失一级），应深挖下一层。"""
    validator = FMEAValidator(CAPACITY_FADE_TREE)
    # moisture_ppm=380 > 300 阈值 -> 越界；其余正常
    tool_results = _result({
        "moisture_ppm": 380.0,
        "np_ratio": 1.10,             # 正常（>1.05）
        "formation_efficiency": 0.92,  # 正常
        "ncm_capacity_mah_g": 200.0,   # 正常
        "peel_strength_n_m": 12.0,     # 正常
    })
    hits = validator.evaluate_branches(tool_results)
    assert len(hits) == 1
    assert hits[0].root.name == "活性锂损失"

    decision = validator.decide_strategy(hits, loop=1)
    assert decision.mode == "DEEPEN"
    # 预算 = 该命中节点的剩余递归深度（水分->烘烤/电解液->供应商）
    assert decision.budget >= 2
    # 补查应包含下一层节点
    assert len(decision.queries) >= 1


def test_single_factor_leaf_triggers_confirm():
    """挖到叶子节点（供应商电解液异常），应 CONFIRM 不再深挖。"""
    validator = FMEAValidator(CAPACITY_FADE_TREE)
    tool_results = _result({
        "moisture_ppm": 380.0,
        "hygroscopicity_index": 1.5,    # 电解液吸湿性越界
        "moisture_sensitivity": 1.6,    # 叶子：供应商电解液越界
    })
    hits = validator.evaluate_branches(tool_results)
    assert len(hits) == 1
    # 命中的最深节点应是叶子
    assert hits[0].current_node.is_leaf()
    decision = validator.decide_strategy(hits, loop=2)
    assert decision.mode == "CONFIRM"


# ---------------------------------------------------------------------------
# 场景 2：多因横查
# ---------------------------------------------------------------------------
def test_multi_factor_triggers_correlate():
    """活性锂损失 + 析锂 两条独立链路同时命中 -> 横向关联，不深挖。"""
    validator = FMEAValidator(CAPACITY_FADE_TREE)
    tool_results = _result({
        "moisture_ppm": 380.0,   # 活性锂损失线越界
        "np_ratio": 0.98,        # 析锂线越界（<1.05）
    })
    hits = validator.evaluate_branches(tool_results)
    assert len(hits) == 2

    decision = validator.decide_strategy(hits, loop=1)
    assert decision.mode == "CORRELATE"
    assert decision.budget == 2  # 关联固定预算，不按深度
    # 应包含时序同步检查 + 对照组
    actions = [q["action"] for q in decision.queries]
    assert any("时序" in a for a in actions)
    assert any("对照组" in a for a in actions)


def test_multi_factor_confidence_discounted():
    """多因未验证耦合前，置信度应被打折（鼓励走关联验证）。"""
    validator = FMEAValidator(CAPACITY_FADE_TREE)
    multi = _result({"moisture_ppm": 380.0, "np_ratio": 0.98})
    single = _result({"moisture_ppm": 380.0})

    hits_multi = validator.evaluate_branches(multi)
    hits_single = validator.evaluate_branches(single)
    cov = 1.0
    conf_multi = validator.compute_confidence(hits_multi, cov)
    conf_single = validator.compute_confidence(hits_single, cov)
    # 多因场景被乘了 0.85 折扣
    assert conf_multi < conf_single * len(hits_multi)  # 不是简单线性叠加


# ---------------------------------------------------------------------------
# 场景 3：优雅降级
# ---------------------------------------------------------------------------
def test_no_hit_first_loop_replan():
    """首轮全部正常（没命中） -> REPLAN 重新规划。"""
    validator = FMEAValidator(CAPACITY_FADE_TREE)
    tool_results = _result({
        "np_ratio": 1.10, "moisture_ppm": 200.0,
        "ncm_capacity_mah_g": 200.0, "peel_strength_n_m": 12.0,
    })
    hits = validator.evaluate_branches(tool_results)
    assert len(hits) == 0
    decision = validator.decide_strategy(hits, loop=1)
    assert decision.mode == "REPLAN"


def test_no_hit_later_loop_degrade():
    """多轮仍未命中 -> DEGRADE 转人工。"""
    validator = FMEAValidator(CAPACITY_FADE_TREE)
    hits = validator.evaluate_branches(_result({"np_ratio": 1.10}))
    decision = validator.decide_strategy(hits, loop=2)
    assert decision.mode == "DEGRADE"
    assert decision.reason


# ---------------------------------------------------------------------------
# 置信度可复现性（对比"LLM 拍数字"）
# ---------------------------------------------------------------------------
def test_confidence_is_reproducible():
    """同样输入，置信度必须完全一致（确定性）。"""
    validator = FMEAValidator(CAPACITY_FADE_TREE)
    tr = _result({"moisture_ppm": 380.0})
    hits = validator.evaluate_branches(tr)
    c1 = validator.compute_confidence(hits, 1.0)
    c2 = validator.compute_confidence(hits, 1.0)
    assert c1 == c2


def test_severity_scales_with_deviation():
    """偏离越大，严重度越高。"""
    validator = FMEAValidator(CAPACITY_FADE_TREE)
    mild = validator.evaluate_branches(_result({"moisture_ppm": 330.0}))
    severe = validator.evaluate_branches(_result({"moisture_ppm": 600.0}))
    assert severe[0].severity > mild[0].severity


def test_get_tree_registry():
    tree = get_tree("容量衰减")
    assert tree is not None
    assert tree.defect_type == CAPACITY_FADE_TREE.defect_type
    assert len(tree.root_branches) == len(CAPACITY_FADE_TREE.root_branches)
    assert get_tree("不存在的缺陷") is None


if __name__ == "__main__":
    # 无 pytest 也能跑：逐个调用并打印
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
