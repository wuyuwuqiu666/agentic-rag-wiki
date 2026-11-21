"""B2 in-flight 记账验收：问答出口并发计数、越 soft 阈只软告警不阻断、异常仍归零。"""

from __future__ import annotations

import pytest
from src.partitions.memory.service.audit_sink import AuditSink
from src.platform.config import Settings
from src.platform.gateway.inflight import InflightTracker
from src.platform.gateway.policy_gate import PolicyGate
from src.platform.harness.breaker_rules.loader import load_rules


def _gate(tmp_path) -> PolicyGate:
    s = Settings(audit_dir=str(tmp_path / "audit"))
    return PolicyGate(load_rules(), AuditSink(s.audit_dir), shadow_only=s.shadow_breaker_set)


def test_slot_returns_count_and_drains_to_zero(tmp_path) -> None:
    tracker = InflightTracker(_gate(tmp_path))
    assert tracker.acquire() == 1
    assert tracker.count == 1
    tracker.release()
    assert tracker.count == 0


def test_slot_releases_on_exception(tmp_path) -> None:
    tracker = InflightTracker(_gate(tmp_path))
    with pytest.raises(RuntimeError), tracker.slot():
        raise RuntimeError("boom")
    assert tracker.count == 0  # 异常出口仍归零，不虚增并发预算


def test_over_soft_threshold_alarms_but_does_not_block(tmp_path) -> None:
    gate = _gate(tmp_path)
    tracker = InflightTracker(gate)
    audit = gate._audit  # noqa: SLF001 - 测试内检视审计落点
    trips: list[str] = []
    try:
        for _ in range(7):  # b2.yaml soft=6 → 第 7 次在飞越 soft 阈
            tracker.acquire()
    finally:
        for _ in range(7):
            tracker.release()
    trips = [e for e in audit.read_all() if e.kind == "breaker_trip" and e.extra["breaker"] == "B2"]
    assert trips  # 越 soft 阈即软告警留痕
    assert trips[-1].extra["action"] == "alarm"  # B2=soft 档：只告警，绝不 block/抛断
    assert tracker.count == 0
