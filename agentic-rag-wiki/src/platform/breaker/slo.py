"""§15 E5 错误预算 / SLO 降级判定内核（P1 纯函数，零 IO）。

NFR-1 查询面可用性 99.5% 落成 SLO；E5"错误预算耗尽自动降级非关键功能"沿 FR-CAP-2 ④→③→② 阶梯逐级降、
与 B2 共用 supervisor 通道。本模块只锁定确定性计算口径——"窗口计数 → 是否透支 → 降到第几级"——
可对账、可单测；真·逐日 SLO 计算作业、实时可用性喂数与运行期 read 路径自动降档执行依赖真实流量
=§15 E5/运行时放行档（见 AGENTS §8），此处不伪造调度器、不谎称已闭环自动降档。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

# 降级阶梯：0=不降；1..3 沿 FR-CAP-2 逐级降（越深=先弃的非关键功能越多，核心问答最后动）。
MAX_DEGRADE_LEVEL = 3
# fast-burn 触发阈（错误预算透支倍数）：达 1× 起降，越爆降得越深（SRE 多窗快速燃尽口径的简化单窗档）。
_BURN_STEPS: tuple[float, ...] = (1.0, 2.0, 4.0)


@dataclass(frozen=True)
class ErrorBudget:
    """一个观测窗口内的错误预算（SLO 允许坏额 vs 实际坏额）。构造即校验外部计数（T2 不可信输入先过门）。"""

    target: float  # 可用性 SLO，如 0.995
    total: int  # 窗口内查询面请求总数
    bad: int  # 违约请求数（超 SLI：错误或非达标延迟）

    def __post_init__(self) -> None:
        if not 0.0 < self.target < 1.0:
            raise ValueError(f"SLO target 须落在 (0,1)，实得 {self.target}")
        if self.total < 0:
            raise ValueError("窗口请求总数不得为负")
        if self.bad < 0 or self.bad > self.total:
            raise ValueError(f"坏请求数须在 [0, total]，实得 bad={self.bad}, total={self.total}")

    @property
    def allowed_bad(self) -> int:
        """窗口可违约量=ceil((1-target)*total)（预算向上取整，避免小额度被截成 0 致永不胜任）。

        先 round 消除浮点尾噪（如 1-0.995=0.005000…004 会使 10000× 越 50 取整成 51），保证口径可复算、可对账。
        """
        if self.total <= 0:
            return 0
        return ceil(round((1.0 - self.target) * self.total, 9))

    @property
    def remaining(self) -> int:
        return max(self.allowed_bad - self.bad, 0)

    @property
    def burn_ratio(self) -> float:
        """坏额已用预算倍数（>=1 即透支）；allowed_bad=0（额度极小窗口）时任何坏即爆表。"""
        if self.allowed_bad <= 0:
            return float("inf") if self.bad > 0 else 0.0
        return self.bad / self.allowed_bad

    @property
    def exhausted(self) -> bool:
        return self.bad > 0 and self.bad >= self.allowed_bad


@dataclass(frozen=True)
class DegradePlan:
    level: int  # 0=不降；1..MAX_DEGRADE_LEVEL=沿 FR-CAP-2 降至第几级
    exhausted: bool
    reason: str


def degrade_level(budget: ErrorBudget, *, steps: tuple[float, ...] = _BURN_STEPS) -> int:
    """未透支→0；随透支倍数沿阶梯逐级加深降级（确定性、单调）。"""
    burn = budget.burn_ratio
    level = 0
    for i, threshold in enumerate(steps, start=1):
        if burn >= threshold:
            level = i
    return min(level, MAX_DEGRADE_LEVEL)


def assess(*, target: float, total: int, bad: int, steps: tuple[float, ...] = _BURN_STEPS) -> DegradePlan:
    """纯决策计算：给定窗口计数返回降级方案（不含调度、不含执行动作——两者属运行时放行档）。"""
    budget = ErrorBudget(target=target, total=total, bad=bad)
    level = degrade_level(budget, steps=steps)
    if level == 0:
        return DegradePlan(level=0, exhausted=budget.exhausted, reason="")
    reason = (
        f"错误预算已透支 {budget.burn_ratio:g}×（坏 {budget.bad}/允许 {budget.allowed_bad}）"
        f"→沿 FR-CAP-2 降至第 {level} 级（先弃非关键功能，核心问答最后动）"
    )
    return DegradePlan(level=level, exhausted=budget.exhausted, reason=reason)
