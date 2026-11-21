"""熔断判定内核单测（零 IO，纯函数；P4 自带验证）。"""

from __future__ import annotations

from src.platform.breaker import evaluate, evaluate_b0
from src.platform.breaker.rules import Rule
from src.platform.types.breaker import BreakerLevel, TripRecord

_B1 = Rule(breaker="B1", metric="batch_delete_ratio", comparator=">=", soft=0.01, hard=0.10, level=BreakerLevel.HARD, reason="批量删除")
_B4 = Rule(breaker="B4", metric="injection_score", comparator=">=", soft=0.6, hard=0.8, level=BreakerLevel.SHADOW, reason="注入")
_B2 = Rule(breaker="B2", metric="inflight_llm", comparator=">=", soft=6, hard=8, level=BreakerLevel.SOFT, reason="在飞")


def test_below_soft_not_tripped() -> None:
    d = evaluate("batch_delete_ratio", 0.005, _B1)
    assert not d.tripped and d.action == "none"


def test_cross_soft_alarms_under_hard_gate() -> None:
    d = evaluate("batch_delete_ratio", 0.05, _B1)
    assert d.tripped and d.action == "alarm" and d.threshold == 0.01


def test_cross_hard_blocks() -> None:
    d = evaluate("batch_delete_ratio", 0.2, _B1)
    assert d.tripped and d.action == "block" and d.threshold == 0.10


def test_shadow_gate_only_observes() -> None:
    d = evaluate("injection_score", 0.9, _B4)
    assert d.tripped and d.action == "observe"


def test_soft_gate_hard_cross_still_alarm() -> None:
    d = evaluate("inflight_llm", 9, _B2)
    assert d.tripped and d.action == "alarm"


def test_metric_mismatch_passes_through() -> None:
    d = evaluate("other_metric", 999.0, _B1)
    assert not d.tripped


def test_b0_escalates_at_three_unresolved() -> None:
    trips = [TripRecord(breaker="B1", tripped_at=i) for i in range(3)]
    d = evaluate_b0(trips)
    assert d.tripped and d.action == "block" and set(d.escalated_from) == {"B1"}


def test_b0_quiet_below_threshold() -> None:
    trips = [TripRecord(breaker="B1", tripped_at=0, reset_at=1), TripRecord(breaker="B3", tripped_at=2)]
    assert not evaluate_b0(trips).tripped
