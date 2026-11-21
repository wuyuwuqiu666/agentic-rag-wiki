"""§8.5 B2 分池预算（v0.6）离线判定内核契约测试：三本独立账 + 峰时单向借出（问答优先）+ 自动归还。

覆盖四条执法规则（①自有额度不可侵 ②借出只借闲置且不越全局硬上限 ③峰时单向=只问答可借 ④借占自动归还可观测）、
额度与 B2/Settings 的单一口径不变式，以及账本·问答出口（`InflightTracker` 池化视图）的端到端行为。
执行档真实 LLM 出口仅问答一路（wiki 编译/进化作业 dev 档不调 LLM=池在册无自有流量），故离线池借占拒绝路径
以合成在飞状态断言；真实离线作业上线即自动受同一账本执法。额度切分 5/2/1 系设计初值（须 shadow 定标+ADR）。
"""

from __future__ import annotations

import pytest
from src.platform.breaker.pool import ALL_POOLS, DEFAULT_OWN, DEFAULT_PEAK, PoolBudget, PoolLedger, decide
from src.platform.config.settings import Settings
from src.platform.gateway.composition import build_container
from src.platform.gateway.inflight import InflightTracker
from src.platform.harness.breaker_rules.loader import load_rules


def _b2_soft() -> float:
    return next(r.soft for r in load_rules() if r.breaker == "B2")  # type: ignore[return-value]


def test_three_independent_books_and_capacity_single_source() -> None:
    b = PoolBudget()
    assert set(b.own) == set(ALL_POOLS)  # 问答/wiki 编译/进化作业三本独立账
    assert b.hard == sum(b.own.values()) == Settings.model_fields["inflight_llm_limit"].default == 8
    assert b.peak == _b2_soft() == DEFAULT_PEAK  # 峰水位=B2 soft 阈，不新造第二套数
    assert b.own["qa"] > b.own["wiki_compile"] > b.own["evolve"]  # 问答优先的额度侧体现


def test_for_hard_scales_without_inventing_capacity() -> None:
    assert PoolBudget.for_hard(8) == PoolBudget()  # 执行档逐字等于出厂初值
    b50 = PoolBudget.for_hard(50)  # 企业档外推：只按权重整数分配，总量不增
    assert sum(b50.own.values()) == 50 and b50.own["qa"] == 31 and b50.peak == 38
    with pytest.raises(ValueError, match="三本独立账"):
        PoolBudget(own={"qa": 8}, hard=8)
    with pytest.raises(ValueError, match="全局硬上限须等于三本账之和"):
        PoolBudget(own=DEFAULT_OWN, hard=9)
    with pytest.raises(ValueError, match="不足以给三本账各留 1 槽"):
        PoolBudget.for_hard(2)


def test_rule1_own_budget_is_inviolable_even_after_lending() -> None:
    b = PoolBudget()
    state = {"qa": 5, "wiki_compile": 0, "evolve": 1}  # 问答把 wiki 的闲置借走了（全局 6）
    v = decide(b, state, "wiki_compile")
    assert v.granted and not v.borrowed  # 出借方回来取自己的额度：永远优先，不因地面被占而拒
    assert "自有额度内" in v.reason


def test_rule3_borrow_is_one_way_at_peak() -> None:
    b = PoolBudget()
    state = {"qa": 5, "wiki_compile": 2, "evolve": 0}  # 全局 7 ≥ 峰水位 6=达峰
    qa = decide(b, state, "qa")
    assert qa.granted and qa.borrowed and "临时借出" in qa.reason  # 问答可借离线池闲置（问答优先）
    wiki = decide(b, state, "wiki_compile")
    assert not wiki.granted and "峰时单向借出" in wiki.reason  # 离线池峰时不得借占=防饿死在线流量
    off_peak = decide(b, {"qa": 3, "wiki_compile": 2, "evolve": 0}, "wiki_compile")
    assert off_peak.granted and off_peak.borrowed  # 平峰离线池可借空闲（未被条款禁止）


def test_rule2_borrow_respects_global_hard_when_strict() -> None:
    b = PoolBudget(own={"qa": 1, "wiki_compile": 1, "evolve": 1}, hard=3, peak=3)
    v = decide(b, {"qa": 3, "wiki_compile": 0, "evolve": 0}, "qa", strict=False)
    assert v.granted and "全局硬上限" not in v.reason  # 在线池 soft 口径：越硬上限只告警不拒
    offline = decide(b, {"qa": 1, "wiki_compile": 1, "evolve": 1}, "evolve")
    assert not offline.granted and "他池无闲置可借" in offline.reason  # 都被自己账占着→停非关键任务


def test_rule4_online_pool_is_never_blocked_by_budget() -> None:
    b = PoolBudget()
    saturated = {"qa": 5, "wiki_compile": 2, "evolve": 1}
    qa = decide(b, saturated, "qa", strict=False)
    assert qa.granted and qa.borrowed and "软告警" in qa.reason  # B2=alarm 不阻断在线（承 FR-CAP-2 阶梯次序）
    assert not decide(b, saturated, "evolve", strict=True).granted  # 同一状态离线池被拒=不对称执法


def test_ledger_books_three_pools_and_auto_returns_borrow() -> None:
    seen: list[int] = []
    ledger = PoolLedger(PoolBudget(), report=seen.append)
    holds = [ledger.slot("qa") for _ in range(7)]  # 问答自有 5 + 借占 2
    with holds[0], holds[1], holds[2], holds[3], holds[4], holds[5], holds[6]:
        assert ledger.inflight("qa") == 7 and ledger.inflight("wiki_compile") == 0
        assert ledger.outstanding_borrow("qa") == 2  # ④借占窗口可观测
        assert ledger.total() == max(seen) == 7 and ledger.budget.hard == 8
    assert ledger.total() == 0 and ledger.outstanding_borrow("qa") == 0  # 跑完即自动归还，无人工调额
    snap = ledger.snapshot()
    assert snap["own"] == dict(DEFAULT_OWN) and snap["at_peak"] is False


def test_ledger_denies_offline_borrow_at_peak_end_to_end() -> None:
    ledger = PoolLedger(PoolBudget())
    for _ in range(4):
        assert ledger.acquire("qa").granted
    for _ in range(2):
        assert ledger.acquire("wiki_compile").granted  # wiki 用满自有 2 槽，全局 6=达峰水位
    assert ledger.snapshot()["at_peak"] is True
    assert ledger.acquire("evolve").granted  # ①进化池取自己的额度：不可侵（哪怕全局已到 7）
    denied = ledger.acquire("wiki_compile")  # ③wiki 再要第 3 槽=只能借占，峰时被单向条款拒
    assert not denied.granted and "峰时单向借出" in denied.reason
    assert ledger.acquire("qa").granted  # 同一时刻问答仍可借=问答优先


def test_qa_tracker_is_online_pool_view_of_shared_ledger() -> None:
    class _Guard:
        def __init__(self) -> None:
            self.values: list[float] = []

        def check(self, metric: str, value: float, breaker: str, *, partition: str = "platform") -> None:
            assert (metric, breaker) == ("inflight_llm", "B2")  # 上报仍是 B2 全局在飞口径
            self.values.append(value)

        def ensure_write_allowed(self, partition: str = "platform") -> object:
            return None

    guard = _Guard()
    ledger = PoolLedger(PoolBudget())
    tracker = InflightTracker(guard, ledger=ledger)  # 默认 pool="qa"
    with tracker.slot():
        assert tracker.count == 1 and ledger.inflight("qa") == 1
    assert tracker.count == 0 and ledger.inflight("qa") == 0
    slots = [tracker.acquire() for _ in range(6)]  # 越自有额度=借占，但问答永不被预算拒
    assert slots[-1] == 6 and ledger.outstanding_borrow("qa") == 1
    assert guard.values[-1] == 6.0
    for _ in slots:
        tracker.release()
    assert tracker.count == 0


def test_unpooled_tracker_keeps_legacy_single_counter() -> None:
    tracker = InflightTracker()
    assert tracker.acquire() == 1 and tracker.count == 1
    tracker.release()
    assert tracker.count == 0


def test_composition_shares_one_ledger_between_qa_exit_and_admin() -> None:
    """装配证据：问答出口与运维读数读的是**同一册**账本（非各持一套数=分池台账不作废）。"""
    container = build_container(Settings())
    assert container.pools.budget.hard == container.settings.inflight_llm_limit == 8
    assert container.admin.pool_budget()["borrowed"] == dict.fromkeys(ALL_POOLS, 0)
    with container.inflight.slot():
        assert container.admin.pool_budget()["inflight"]["qa"] == 1  # 同一实例：出口记账→运维可见
    assert container.admin.pool_budget()["inflight"]["qa"] == 0
