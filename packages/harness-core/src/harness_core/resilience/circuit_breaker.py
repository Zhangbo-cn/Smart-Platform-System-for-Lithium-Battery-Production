"""熔断器：MCP 下游依赖连续失败 N 次后 OPEN，以概率放行探针快速恢复。

经典熔断器的 HALF_OPEN + 固定 30s 冷却不适合 MCP 内部调用场景——
请求量低、冲击可控、恢复应秒级。改用概率放行：
  CLOSED → (3 次失败) → OPEN(5% 探针率) → 探针成功 → CLOSED
                                           → 探针失败 → 继续 OPEN
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"   # 正常通行
    OPEN = "open"       # 熔断，概率放行探针


@dataclass
class CircuitBreaker:
    """简单熔断器。

    CLOSED:
        所有请求放行。连续 failure_threshold 次失败 → OPEN。
    OPEN:
        仅 probe_rate (≈5%) 的请求作为探针放行，其余直接拒绝。
        探针成功 → CLOSED（秒级恢复）。
        探针失败 → 继续 OPEN，不加计 failure_count（避免累计到 threshold 的误解）。
    """

    name: str
    failure_threshold: int = 3          # 连续失败多少次后熔断
    probe_rate: float = 0.05            # OPEN 状态下放行比例（5%）

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0

    def allow(self) -> bool:
        """返回 True 表示允许请求通过。"""
        if self.state == CircuitState.CLOSED:
            return True
        # OPEN — 概率放行探针
        return random.random() < self.probe_rate

    def success(self) -> None:
        """请求成功。若从 OPEN 恢复 → CLOSED。"""
        if self.state == CircuitState.OPEN:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info("circuit.closed", name=self.name)
        else:
            self.failure_count = 0

    def failure(self) -> None:
        """请求失败。累计计数达到阈值 → OPEN。"""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "circuit.open",
                name=self.name,
                failures=self.failure_count,
            )


class CircuitRegistry:
    """全局熔断器注册表，按 MCP Server 名或 tool 名管理。"""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name=name)
        return self._breakers[name]

    def all_states(self) -> dict[str, str]:
        return {name: cb.state.value for name, cb in self._breakers.items()}


# 全局单例
_registry: CircuitRegistry | None = None


def get_circuit_registry() -> CircuitRegistry:
    global _registry
    if _registry is None:
        _registry = CircuitRegistry()
    return _registry
