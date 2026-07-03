"""
FMEA 验证器 —— Reflector 的确定性内核。

职责（全部不调 LLM，纯计算，可复现可审计）：
1. evaluate_branches  : 判断哪些因果链路命中（数据异常）
2. compute_confidence : 用加权公式算置信度，而不是让 LLM 拍一个数字
3. decide_strategy    : 根据"命中几条链 / 各自多深"决定补查策略
                        - 0 条命中 -> 重规划 / 降级
                        - 1 条命中 -> 单因深挖（预算 = 剩余递归深度）
                        - ≥2 条命中 -> 多因耦合，横向关联（预算固定 1-2 轮）

这是把"补查次数 = 命中链路深度，多因走横向"这一设计落地的地方。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from knowledge.fmea_tree import FMEANode, FMEATree


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class HitBranch:
    """一条命中的因果链路。"""

    root: FMEANode  # 所属 root 分支（析锂/活性锂/...）
    current_node: FMEANode  # 当前挖到的最深异常节点
    severity: float  # 偏离显著度（>1 表示越界，越大越严重）
    metric_value: float | None = None


@dataclass
class RefineDecision:
    """补查决策。"""

    mode: str  # DEEPEN / CORRELATE / REPLAN / CONFIRM / DEGRADE
    budget: int = 0  # 本策略允许的补查轮次上限
    queries: list[dict[str, Any]] = field(default_factory=list)
    target: HitBranch | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# 验证器
# ---------------------------------------------------------------------------
class FMEAValidator:
    def __init__(self, tree: FMEATree, hitl_threshold: float = 0.7) -> None:
        self.tree = tree
        self.hitl_threshold = hitl_threshold

    # ===== 1. 命中判断 =====
    def evaluate_branches(self, tool_results: list[dict[str, Any]]) -> list[HitBranch]:
        """扫描所有 root 分支，找出数据异常的链路。每条 root 分支最多产出一个 HitBranch
        （取该分支里挖得最深的那个异常节点作为'当前下钻位置'）。"""
        metrics = self._flatten_metrics(tool_results)
        hits: list[HitBranch] = []

        for branch in self.tree.root_branches:
            anomaly_nodes: list[tuple[FMEANode, float, float]] = []
            self._collect_anomalies(branch, metrics, anomaly_nodes)
            if not anomaly_nodes:
                continue
            # 取最深的异常节点：用在树中的深度排序
            deepest = max(anomaly_nodes, key=lambda t: self.tree.remaining_depth(branch) - self.tree._depth(t[0]))
            node, severity, value = deepest
            hits.append(HitBranch(root=branch, current_node=node, severity=severity, metric_value=value))
        return hits

    def _collect_anomalies(
            self,
            node: FMEANode,
            metrics: dict[str, float],
            out: list[tuple[FMEANode, float, float]],
    ) -> None:
        """递归收集一条分支里所有'已取证且越界'的节点。"""
        if node.metric_key and node.abnormal_when and node.metric_key in metrics:
            value = metrics[node.metric_key]
            severity = self._severity(value, node.abnormal_when)
            if severity > 1.0:  # 越界
                out.append((node, severity, value))
        for child in node.children:
            self._collect_anomalies(child, metrics, out)

    @staticmethod
    def _severity(value: float, abnormal_when: tuple[str, float]) -> float:
        """偏离显著度。>1 越界。例：阈值100、实测130、direction='>' -> 1.3。"""
        direction, threshold = abnormal_when
        if threshold == 0:
            return 2.0 if (value > 0 if direction == ">" else value < 0) else 0.0
        if direction == ">":
            return value / threshold
        else:  # "<"
            return threshold / value if value != 0 else 2.0

    @staticmethod
    def _flatten_metrics(tool_results: list[dict[str, Any]]) -> dict[str, float]:
        """把所有工具返回里的数值指标拍平成 {metric_key: value}，供命中判断查阅。"""
        metrics: dict[str, float] = {}

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        metrics[k] = float(v)
                    else:
                        walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        for record in tool_results:
            walk(record.get("result") if isinstance(record, dict) else record)
        return metrics

    # ===== 2. 置信度（加权公式，非 LLM 拍数）=====
    def compute_coverage(self, tool_results: list[dict[str, Any]]) -> float:
        """证据覆盖率 = 已取证的 root 分支数 / 总 root 分支数。"""
        metrics = self._flatten_metrics(tool_results)
        covered = 0
        for branch in self.tree.root_branches:
            if self._branch_probed(branch, metrics):
                covered += 1
        return covered / len(self.tree.root_branches) if self.tree.root_branches else 0.0

    def _branch_probed(self, node: FMEANode, metrics: dict[str, float]) -> bool:
        if node.metric_key and node.metric_key in metrics:
            return True
        return any(self._branch_probed(c, metrics) for c in node.children)

    def compute_confidence(self, hits: list[HitBranch], coverage: float) -> float:
        """
        P(根因|证据) 的工程近似：
          - 覆盖率不足 -> 整体置信度按比例打折（没查全不敢自信）
          - 命中链路的严重度按权重累加
          - 多因并存时单独任一链路解释力下降 -> 轻微折扣，鼓励走耦合验证
        """
        if not hits:
            return 0.0
        evidence_strength = 0.0
        for h in hits:
            # severity 截到 [1,3]，归一化到 [0,1]，再乘节点权重
            norm = min(max(h.severity, 1.0), 3.0) / 3.0
            evidence_strength += norm * h.current_node.weight
        evidence_strength = min(evidence_strength, 1.0)

        conf = evidence_strength * coverage
        if len(hits) >= 2:
            conf *= 0.85  # 多因未验证耦合前，压一档
        return round(min(conf, 1.0), 3)

    # ===== 3. 补查策略决策 =====
    def decide_strategy(self, hits: list[HitBranch], loop: int) -> RefineDecision:
        n = len(hits)

        # ---- 情况 0：没命中 ----
        if n == 0:
            if loop == 1:
                return RefineDecision(mode="REPLAN", budget=self.tree.max_depth(),
                                      reason="首轮未命中已知因果链，重新规划取证方向")
            return RefineDecision(mode="DEGRADE", reason="多轮仍未命中任何已知因果链，疑似知识盲区")

        # ---- 情况 1：单链命中 -> 深度下钻 ----
        if n == 1:
            hit = hits[0]
            if hit.current_node.is_leaf():
                return RefineDecision(mode="CONFIRM", target=hit,
                                      reason="已下钻至根因层，准备出具结论")
            children = self.tree.get_children(hit.current_node)
            queries = [c.tool_call() for c in children if c.tool]
            return RefineDecision(
                mode="DEEPEN",
                budget=self.tree.remaining_depth(hit.current_node),  # 预算 = 剩余递归深度
                queries=queries,
                target=hit,
                reason=f"单因链路命中（{hit.root.name}），沿因果链下钻一层",
            )

        # ---- 情况 2：多链命中 -> 横向关联（不深挖）----
        return RefineDecision(
            mode="CORRELATE",
            budget=2,  # 关联验证 1-2 轮足够，不按深度
            queries=self._build_correlation_queries(hits),
            reason=f"{n} 条独立链路同时命中，疑似多因耦合，转横向关联验证",
        )

    def _build_correlation_queries(self, hits: list[HitBranch]) -> list[dict[str, Any]]:
        """多因耦合时的横向验证查询：时序同步 + 对照组隔离。"""
        queries: list[dict[str, Any]] = []
        # 时序同步：几条链的异常是否发生在同一时间窗口
        queries.append({
            "tool": "scada.detect_anomaly_window",
            "tool_args": {"check": "temporal_overlap",
                          "targets": [h.current_node.name for h in hits]},
            "action": "验证多因异常的时序同步性",
        })
        # 对照组：隔离单个因子，看是否仍致命
        for h in hits:
            queries.append({
                "tool": h.current_node.tool or "lims.query_process_test",
                "tool_args": {"check": "control_group", "isolate_factor": h.root.name},
                "action": f"对照组验证：仅 {h.root.name} 存在时的影响",
            })
        return queries

    # ===== 4. FTA 顶事件闭合（与 FMEA 因果树共用图结构）=====
    def validate_fta_closure(self, hits: list[HitBranch]) -> tuple[bool, str]:
        """
        FTA 顶事件闭合校验：至少一条命中链到达叶节点（基本事件层）。
        未闭合时不应自动结案，应继续 DEEPEN 或转 HITL。
        """
        if not hits:
            return False, "no_branch_hits"
        for hit in hits:
            if hit.current_node.is_leaf():
                return True, f"leaf_reached:{hit.current_node.name}"
        deepest = max(hits, key=lambda h: self.tree.remaining_depth(h.root) - self.tree._depth(h.current_node))
        return False, f"incomplete_path:{deepest.current_node.name}"

    def requires_hitl_for_fta(self, hits: list[HitBranch], confidence: float) -> bool:
        closed, _ = self.validate_fta_closure(hits)
        if closed:
            return confidence < self.hitl_threshold
        return True
