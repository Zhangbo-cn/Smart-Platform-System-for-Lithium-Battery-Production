"""
FMEA 因果树 —— 整个 RCA 系统的领域知识地基。

设计要点：
- 一棵树同时被四个 Agent 消费：
    Planner   按 root_branches 拆排查计划
    Executor  按 parallel 分组并行取证
    Reflector 按命中节点判断"深挖 / 横向关联 / 降级"
    Reporter  从命中节点取 recommendation 生成 8D 建议
- "宽度"（root_branches 并列分支）由 Executor 一轮并行解决；
  "深度"（children 逐层下钻）才是 Reflector 补查循环消耗的轮次。
- 每个节点带 normal_range（判断异常的依据）和 tool（去哪个 MCP 取证）。

更新这棵树 = 更新全系统行为，无需改 Agent 代码。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FMEANode:
    """因果树的一个节点：一个可能的原因。"""

    name: str
    # 取证：去哪个 MCP 工具、带什么参数、读哪个返回字段
    tool: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    metric_key: str | None = None  # 在工具返回里定位待判断的指标，如 "coating_thickness_std_um"
    # 异常判定：(direction, threshold)，direction ∈ {">", "<"}
    # 例：("<", 1.05) 表示该指标 < 1.05 视为异常（N/P 比偏低）
    abnormal_when: tuple[str, float] | None = None
    weight: float = 1.0  # 该因子对父级缺陷的贡献权重（置信度加权用）
    recommendation: dict[str, str] = field(default_factory=dict)  # {"immediate":..,"long_term":..}
    children: list["FMEANode"] = field(default_factory=list)

    def is_leaf(self) -> bool:
        return not self.children

    def tool_call(self) -> dict[str, Any]:
        """转成 Executor 可执行的 PlanStep 片段。"""
        return {"tool": self.tool, "tool_args": dict(self.tool_args), "action": f"排查：{self.name}"}


class FMEATree:
    """某个缺陷类型的完整因果树。"""

    def __init__(self, defect_type: str, root_branches: list[FMEANode]) -> None:
        self.defect_type = defect_type
        self.root_branches = root_branches
        # 建立 name -> (node, 所属root分支, 在分支内的深度) 的索引，便于 O(1) 查找
        self._index: dict[str, tuple[FMEANode, FMEANode, int]] = {}
        for branch in root_branches:
            self._build_index(branch, branch, depth=1)

    def _build_index(self, node: FMEANode, root: FMEANode, depth: int) -> None:
        self._index[node.name] = (node, root, depth)
        for child in node.children:
            self._build_index(child, root, depth + 1)

    # ---------- 深度计算：补查预算的来源 ----------
    def max_depth(self) -> int:
        """全树最大深度（还没命中任何分支时给的预算）。"""
        return max(self._depth(b) for b in self.root_branches)

    def _depth(self, node: FMEANode) -> int:
        if node.is_leaf():
            return 1
        return 1 + max(self._depth(c) for c in node.children)

    def remaining_depth(self, node: FMEANode) -> int:
        """从某节点往下还能挖几层（单因深挖的预算）。"""
        return self._depth(node)

    # ---------- 查找与遍历 ----------
    def find(self, name: str) -> FMEANode | None:
        hit = self._index.get(name)
        return hit[0] if hit else None

    def root_of(self, name: str) -> FMEANode | None:
        """某节点属于哪条 root 分支（判断'几条独立链路命中'用）。"""
        hit = self._index.get(name)
        return hit[1] if hit else None

    def get_children(self, node: FMEANode) -> list[FMEANode]:
        """取下一层子节点（深挖时补查这些）。"""
        return node.children

    def all_leaf_names(self) -> list[str]:
        return [n for n, (node, _, _) in self._index.items() if node.is_leaf()]

    def first_layer_calls(self) -> list[dict[str, Any]]:
        """Planner 用：每条 root 分支的第一个可取证节点，组成初始并行计划。"""
        calls = []
        for i, branch in enumerate(self.root_branches, start=1):
            leaf = self._first_probeable(branch)
            if leaf and leaf.tool:
                call = leaf.tool_call()
                call["step_id"] = i
                call["parallel"] = True  # root 分支之间天然独立 → 并行
                call["causal_path"] = f"{branch.name} → {leaf.name}"
                calls.append(call)
        return calls

    def _first_probeable(self, node: FMEANode) -> FMEANode | None:
        """找一条分支里第一个能取证（带 tool）的节点。"""
        if node.tool:
            return node
        for child in node.children:
            found = self._first_probeable(child)
            if found:
                return found
        return None


# ============================================================
# 容量衰减（循环 500 次后保持率 < 85%）的真实因果树
# 4 大并列分支（宽度），每条分支往下 1-3 层（深度）
# ============================================================

CAPACITY_FADE_TREE = FMEATree(
    defect_type="容量衰减",
    root_branches=[
        # ---- A. 负极析锂（最常见 ~40%）----
        FMEANode(
            name="负极析锂",
            children=[
                FMEANode(
                    name="负极容量裕度不足_NP比偏低",
                    tool="scada.query_equipment_timeseries",
                    tool_args={"process": "涂布", "sensor_tags": ["np_ratio"]},
                    metric_key="np_ratio",
                    abnormal_when=("<", 1.05),
                    weight=0.5,
                    recommendation={
                        "immediate": "复核正负极涂布面密度配比，调整 N/P 比至 1.08-1.15",
                        "long_term": "在涂布工序加入 N/P 比在线计算与 SPC 监控",
                    },
                ),
                FMEANode(
                    name="充电电流过大",
                    tool="scada.query_equipment_timeseries",
                    tool_args={"process": "化成", "sensor_tags": ["charge_current_c"]},
                    metric_key="charge_current_c",
                    abnormal_when=(">", 0.5),
                    weight=0.3,
                    recommendation={
                        "immediate": "降低化成/充电电流至 0.3C 以下重新评估",
                        "long_term": "建立分容充电曲线库，按体系匹配电流上限",
                    },
                ),
                FMEANode(
                    name="低温充电",
                    tool="scada.query_equipment_timeseries",
                    tool_args={"sensor_tags": ["ambient_temp_c"]},
                    metric_key="ambient_temp_c",
                    abnormal_when=("<", 10.0),
                    weight=0.2,
                    recommendation={"immediate": "禁止低温环境充电，加装环境温控"},
                ),
            ],
        ),
        # ---- B. 活性锂损失（~30%）—— 这是深分支，往下挖 3 层 ----
        FMEANode(
            name="活性锂损失",
            children=[
                FMEANode(
                    name="水分超标副反应",
                    tool="lims.query_process_test",
                    tool_args={"process": "注液前", "metric": "moisture_ppm"},
                    metric_key="moisture_ppm",
                    abnormal_when=(">", 300.0),
                    weight=0.5,
                    children=[
                        FMEANode(
                            name="注液前烘烤不充分",
                            tool="scada.query_equipment_timeseries",
                            tool_args={"process": "烘烤", "sensor_tags": ["bake_temp_c", "bake_duration_s"]},
                            metric_key="bake_temp_c",
                            abnormal_when=("<", 85.0),
                            weight=0.5,
                            recommendation={
                                "immediate": "提高注液前真空烘烤温度/时间，复测水分",
                                "long_term": "烘烤工序加水分在线检测联锁",
                            },
                        ),
                        FMEANode(
                            name="电解液吸湿性偏高",
                            tool="erp.query_material_batch",
                            tool_args={"material": "电解液"},
                            metric_key="hygroscopicity_index",
                            abnormal_when=(">", 1.2),
                            weight=0.5,
                            children=[
                                FMEANode(
                                    name="供应商电解液质量异常",
                                    tool="lims.query_material_test",
                                    tool_args={"material": "电解液"},
                                    metric_key="moisture_sensitivity",
                                    abnormal_when=(">", 1.2),
                                    weight=1.0,
                                    recommendation={
                                        "immediate": "隔离该批次电解液，切换备用合格供应商",
                                        "long_term": "新增电解液吸湿性来料检验项 + 供应商质量评级",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
                FMEANode(
                    name="首效偏低_SEI不致密",
                    tool="scada.query_equipment_timeseries",
                    tool_args={"process": "化成", "sensor_tags": ["formation_efficiency"]},
                    metric_key="formation_efficiency",
                    abnormal_when=("<", 0.88),
                    weight=0.5,
                    recommendation={
                        "immediate": "优化化成首充小电流台阶，提升 SEI 致密度",
                        "long_term": "化成工艺 DOE，锁定首效最优窗口",
                    },
                ),
            ],
        ),
        # ---- C. 正极结构衰退（~20%）----
        FMEANode(
            name="正极结构衰退",
            children=[
                FMEANode(
                    name="正极材料批次问题",
                    tool="lims.query_material_test",
                    tool_args={"material": "NCM"},
                    metric_key="ncm_capacity_mah_g",
                    abnormal_when=("<", 195.0),
                    weight=0.6,
                    recommendation={
                        "immediate": "隔离该 NCM 批次，调取来料 COA 复检克容量",
                        "long_term": "提高正极材料克容量来料抽检频次",
                    },
                ),
                FMEANode(
                    name="过充导致晶格塌陷",
                    tool="scada.query_equipment_timeseries",
                    tool_args={"process": "化成", "sensor_tags": ["charge_voltage_upper"]},
                    metric_key="charge_voltage_upper",
                    abnormal_when=(">", 4.25),
                    weight=0.4,
                    recommendation={"immediate": "下调充电截止电压至体系上限以内"},
                ),
            ],
        ),
        # ---- D. 内阻增长（~10%）----
        FMEANode(
            name="内阻增长",
            children=[
                FMEANode(
                    name="极片粘接失效",
                    tool="lims.query_process_test",
                    tool_args={"process": "辊压", "metric": "peel_strength_n_m"},
                    metric_key="peel_strength_n_m",
                    abnormal_when=("<", 8.0),
                    weight=0.6,
                    recommendation={
                        "immediate": "复核辊压压力与粘结剂比例，复测剥离强度",
                        "long_term": "辊压工序加剥离强度抽检",
                    },
                ),
                FMEANode(
                    name="集流体腐蚀",
                    tool="lims.query_material_test",
                    tool_args={"material": "电解液", "metric": "acidity_hf_ppm"},
                    metric_key="acidity_hf_ppm",
                    abnormal_when=(">", 50.0),
                    weight=0.4,
                    recommendation={"immediate": "检测电解液 HF 含量，更换合格批次"},
                ),
            ],
        ),
    ],
)


# 缺陷类型 -> 因果树 的总注册表（其他缺陷可继续扩展）
# 生产环境优先从 Neo4j/CSV 加载，见 knowledge/fmea_registry.py
FMEA_TREES: dict[str, FMEATree] = {
    "容量衰减": CAPACITY_FADE_TREE,
}


def get_tree(defect_type: str) -> FMEATree | None:
    """Deprecated: use knowledge.fmea_registry.get_tree (Neo4j > CSV > builtin)."""
    from knowledge.fmea_registry import get_tree as registry_get_tree

    return registry_get_tree(defect_type)
