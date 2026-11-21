"""FR-CAP-1 消费漏斗层级归属 + 命中率**稳态口径**内核（M4-ax）：分子/分母/滚动窗/达标判定的机检面。

存在的意义=把"语义缓存 ≥30%、wiki ≥25%"从只在文档里承诺、代码里无处测量的口径，落成可调用可断言的
测量与判级核心；数字承诺本身仍按 v0.7 撤下（执行档不据此判成败），故本测试只锁**口径与归属规则**。
"""

from __future__ import annotations

from typing import Any

from src.partitions.ingest.service.ask import cache
from src.partitions.ingest.service.ask.funnel import (
    LAYERS,
    STEADY_STATE_TARGETS,
    FunnelLedger,
    assess,
    classify_layer,
    rate,
)
from src.platform.gateway import build_container
from src.platform.types import Answer, Citation


def _grounded(question: str) -> Answer:
    return Answer(
        question=question,
        text="有据答案",
        citations=[Citation(chunk_id="d#0", doc_id="d", doc_version=1)],
        confidence=0.9,
    )


def test_classify_layer_precedence_matches_funnel_order() -> None:
    """②wiki 优先于③④（同层重排后首引出处决定归属），③④按跳数分。"""
    assert classify_layer(leading_authority="wiki", hops=3) == "wiki"
    assert classify_layer(leading_authority="original", hops=1) == "single_hop"
    assert classify_layer(leading_authority="", hops=2) == "multi_hop"


def test_rate_uses_effective_denominator_and_float_exactness() -> None:
    assert rate(3, 10) == 0.3  # round(…,9) 后恰达 0.30 目标（浮点尾噪不得翻转达标结论）
    assert rate(1, 3) == round(1 / 3, 9)
    assert rate(0, 0) == 0.0  # 零样本→0，不虚构分母


def test_refused_is_counted_but_never_dilutes_hit_rate() -> None:
    """拒答不是任何一层的"命中"→另记 refused、不入分母（否则跨窗/跨档数字不可比）。"""
    ledger = FunnelLedger()
    for _ in range(3):
        ledger.record("cache", ts=100.0)
    for _ in range(7):
        ledger.record("single_hop", ts=100.0)
    for _ in range(5):
        ledger.record_refused(ts=100.0)
    snap = ledger.snapshot()
    assert snap.effective == 10 and snap.refused == 5
    assert snap.rates["cache"] == 0.3 and snap.rates["single_hop"] == 0.7
    assert set(snap.counts) == set(LAYERS)


def test_rolling_window_is_lower_bound_inclusive() -> None:
    ledger = FunnelLedger()
    ledger.record("cache", ts=0.0)
    ledger.record("wiki", ts=100.0)
    recent = ledger.snapshot(window_seconds=50.0, now=100.0)  # 窗=[50,100]
    assert (recent.window_seconds, recent.effective, recent.counts["wiki"], recent.counts["cache"]) == (50.0, 1, 1, 0)
    assert ledger.snapshot(now=100.0).effective == 2  # 全量口径
    assert ledger.snapshot(window_seconds=100.0, now=100.0).effective == 2  # 下界含


def test_assess_verdicts_and_zero_sample_is_not_pass() -> None:
    ledger = FunnelLedger()
    for _ in range(3):
        ledger.record("cache", ts=1.0)
    for _ in range(3):
        ledger.record("wiki", ts=1.0)
    for _ in range(4):
        ledger.record("single_hop", ts=1.0)
    ok = assess(ledger.snapshot())
    assert ok.evaluated is True and ok.unmet == ()  # 0.30/0.30 恰达 ≥30%/≥25%
    assert ok.targets == STEADY_STATE_TARGETS and set(ok.rates) == set(STEADY_STATE_TARGETS)

    empty = assess(FunnelLedger().snapshot())
    assert empty.evaluated is False and empty.unmet == ()  # 无样本不判定，绝不冒充已达标

    starved = FunnelLedger()
    starved.record("single_hop", ts=1.0)
    bad = assess(starved.snapshot())
    assert bad.evaluated is True and set(bad.unmet) == set(STEADY_STATE_TARGETS)


def test_agent_records_layers_and_admin_reads_same_ledger() -> None:
    """端到端接线：①命中经问答链计入 cache 层、跨域未接地→拒答只计 refused；Admin 读回同一册。"""
    container = build_container()
    question = "薪酬制度如何规定"
    container.cache.put(cache.cache_key(question, ["hr"]), _grounded(question))

    container.app.answer(question, user_acl=["hr"])  # 漏斗①命中
    first: dict[str, Any] = container.admin.funnel_stats()
    assert first["effective"] == 1 and first["counts"]["cache"] == 1 and first["refused"] == 0

    container.app.answer(question, user_acl=["fin"])  # 跨域不命中→实链无可见块→拒答
    second: dict[str, Any] = container.admin.funnel_stats()
    assert second["effective"] == 1 and second["refused"] == 1  # 分母不被拒答稀释
    assert second["evaluated"] is True and second["unmet"] == ["wiki"]

    fresh: dict[str, Any] = build_container().admin.funnel_stats()
    assert fresh["evaluated"] is False and fresh["effective"] == 0
