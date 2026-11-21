"""熔断判定内核（P1 纯函数：MetricSample + Rule → BreakerDecision；零 IO）。"""

from __future__ import annotations

from src.platform.breaker.rules import Rule, action_for
from src.platform.types.breaker import BreakerDecision, BreakerId, BreakerLevel, TripRecord


def _breached(value: float, threshold: float, comparator: str) -> bool:
    return value >= threshold if comparator == ">=" else value <= threshold


def evaluate(sample_metric: str, sample_value: float, rule: Rule, ts: float = 0.0) -> BreakerDecision:
    """单样本对单规则判定。未配该指标的规则直接放行（action=none）。"""
    base = BreakerDecision(
        breaker=rule.breaker,
        tripped=False,
        level=rule.level,
        metric=rule.metric,
        value=sample_value,
        ts=ts,
    )
    if sample_metric != rule.metric:
        return base

    crossed: str | None = None
    threshold: float | None = None
    if rule.hard is not None and _breached(sample_value, rule.hard, rule.comparator):
        crossed, threshold = "hard", rule.hard
    elif rule.soft is not None and _breached(sample_value, rule.soft, rule.comparator):
        crossed, threshold = "soft", rule.soft
    if crossed is None:
        return base

    return BreakerDecision(
        breaker=rule.breaker,
        tripped=True,
        action=action_for(rule.level, crossed),
        level=rule.level,
        reason=f"{rule.reason}（{rule.metric}={sample_value:g} 越{crossed}阈 {threshold:g}）".strip(),
        metric=rule.metric,
        value=sample_value,
        threshold=threshold,
        ts=ts,
    )


def evaluate_b0(
    trips: list[TripRecord],
    *,
    consecutive_trigger: int = 3,
    breaker: BreakerId = "B0",
    ts: float = 0.0,
) -> BreakerDecision:
    """B0 总闸升级判定（v0.6 分级）：连续未复位触发数达阈 → 拉闸、保读侧、冻结写侧。"""
    unresolved = [t for t in trips if t.reset_at is None]
    escalated: list[str] = [t.breaker for t in unresolved]
    tripped = len(unresolved) >= consecutive_trigger
    return BreakerDecision(
        breaker=breaker,
        tripped=tripped,
        action="block" if tripped else "none",
        level=BreakerLevel.HARD,
        reason="连续触发达阈→拉总闸（冻结写侧、保读侧、双人复位）" if tripped else "",
        metric="unresolved_trips",
        value=float(len(unresolved)),
        threshold=float(consecutive_trigger),
        escalated_from=escalated,
        ts=ts,
    )
