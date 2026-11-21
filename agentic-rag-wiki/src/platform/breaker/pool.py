"""§8.5 B2 分池预算（v0.6）判定内核（P1 纯函数 + 线程安全账本，零 IO、零第三方）。

条款口径：问答 / wiki 编译 / 进化作业**三本独立账**，峰时只允许**单向临时借出（问答优先）自动归还**，
防离线任务饿死在线流量。本模块把该句锁成四条可机检规则：

① **自有额度不可侵**：池在自身额度内的请求一律放行——出借过闲置的池回来取自己的额度永远优先；
② **借出只借闲置**：超出自有额度的请求只能取他池未占用的空闲，且严格执法时不得突破全局硬上限（B2 hard）；
③ **峰时单向**：全局在飞 ≥ 峰水位（取 B2 `soft` 阈，非新造数）时只有在线池（问答）还能借，
  离线池的借占请求被拒=「问答优先 + 停非关键任务」（B2 处置列既有动作，不新造档位）；
④ **自动归还**：借占不是永久改配——出借池按①即时取回，借方新请求随之停增，在飞调用跑完即自然归还，
  无需人工调额（对应 B2 恢复列"次日刷新或人工调额"之外的自愈路径），`outstanding_borrow` 使该窗口可观测。

诚实边界：执行档当前唯一 LLM 出口=问答（`partitions/ingest/runtime/ask.py`），wiki 编译/进化作业在 dev 档
不调 LLM=其池账在册、闲置可被问答借用、暂无自有流量；真实离线作业上线即自动受同一账本执法。
额度切分（5/2/1）系设计初值，须 shadow 定标 + ADR（承 AGENTS §8 A2 欠账），本模块不声称已定标。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal, NamedTuple

PoolId = Literal["qa", "wiki_compile", "evolve"]
ALL_POOLS: tuple[PoolId, ...] = ("qa", "wiki_compile", "evolve")
ONLINE_POOLS: frozenset[str] = frozenset({"qa"})  # 在线流量池=被保护方（峰时优先）

# 三本独立账的执行档初值：qa 5 / wiki 编译 2 / 进化 1，合计=8=B2 hard=Settings.inflight_llm_limit（单测锁等值防漂移）。
DEFAULT_OWN: Mapping[str, int] = {"qa": 5, "wiki_compile": 2, "evolve": 1}
# 峰水位=B2 soft 阈（harness/breaker_rules/b2.yaml soft=6）；单测锁等值，防"分池阈值与熔断阈值两张皮"。
DEFAULT_PEAK = 6
_DEFAULT_HARD = sum(DEFAULT_OWN.values())  # 8


@dataclass(frozen=True)
class PoolBudget:
    """预算表（外部输入构造期校验，T2）：三本账额度、全局硬上限、峰水位。"""

    own: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_OWN))
    hard: int = _DEFAULT_HARD
    peak: int = DEFAULT_PEAK

    def __post_init__(self) -> None:
        if set(self.own) != set(ALL_POOLS):
            raise ValueError(f"分池预算须为三本独立账 {ALL_POOLS}，实得 {sorted(self.own)}")
        if any(v <= 0 for v in self.own.values()):
            raise ValueError(f"各池自有额度须为正整数：{dict(self.own)}")
        if self.hard != sum(self.own.values()):
            raise ValueError(f"全局硬上限须等于三本账之和（B2 分池不新造容量）：{self.hard} ≠ {sum(self.own.values())}")
        if not 0 < self.peak <= self.hard:
            raise ValueError(f"峰水位须落在 (0, hard]：peak={self.peak} hard={self.hard}")

    @classmethod
    def for_hard(cls, hard: int) -> PoolBudget:
        """按全局硬上限（`Settings.inflight_llm_limit`/B2 hard 同源值）等比定档三本账与峰水位。

        执行档 `hard=8` 时逐字等于出厂初值（单测锁该不变式）；企业档 `hard=50` 不外添容量、只按
        5:2:1 权重用最大余数法整数分配（确定性、可复算），峰水位同比例折算。
        """
        if hard < len(ALL_POOLS):
            raise ValueError(f"全局硬上限 {hard} 不足以给三本账各留 1 槽")
        weights = [(p, DEFAULT_OWN[p]) for p in ALL_POOLS]
        total_w = sum(w for _, w in weights)
        own: dict[str, int] = {}
        remainders: list[tuple[float, str]] = []
        for name, w in weights:
            raw = hard * w / total_w
            own[name] = int(raw)
            remainders.append((raw - int(raw), name))
        for _ in range(hard - sum(own.values())):  # 最大余数法补齐（同余数按 ALL_POOLS 次序，确定性）
            best = max(remainders, key=lambda r: (r[0], -ALL_POOLS.index(r[1])))
            own[best[1]] += 1
            remainders.remove(best)
        peak = max(1, round(hard * DEFAULT_PEAK / _DEFAULT_HARD))
        return cls(own=own, hard=hard, peak=peak)


@dataclass(frozen=True)
class Acquisition:
    granted: bool
    pool: str
    borrowed: bool  # True=本次占的是他池借来的闲置（临时借出，按④自动归还）
    pool_inflight: int  # 判定时的该池在飞（未放行=不变）
    global_inflight: int  # 判定时的全局在飞
    reason: str


class PoolVerdict(NamedTuple):
    granted: bool
    borrowed: bool
    reason: str


def decide(budget: PoolBudget, state: Mapping[str, int], pool: str, *, strict: bool = True) -> PoolVerdict:
    """纯判定：给定额度表与当前在飞快照，回答"这次能否占一个槽、是否借占、为什么"。

    `strict=False`=在线池（问答）口径：B2 是软告警档，额度耗尽仍放行、只由调用方上报越阈告警，
    绝不因预算拒在线请求（拒问答=把容量问题变成用户可见失败，违 FR-CAP-2 降级阶梯次序）。
    """
    if pool not in budget.own:
        raise ValueError(f"未知池：{pool}")
    own = budget.own[pool]
    cur = state.get(pool, 0)
    total = sum(state.values())
    if cur < own:  # 规则①
        return PoolVerdict(True, False, f"{pool} 自有额度内 {cur}/{own}")
    lenders = tuple(p for p in ALL_POOLS if p != pool and state.get(p, 0) < budget.own[p])
    if not lenders:  # 他池无闲置（都被自己的账占着）
        if strict:
            return PoolVerdict(False, False, f"{pool} 超自有额度 {own} 且他池无闲置可借→停该非关键任务")
        return PoolVerdict(True, True, f"{pool} 超自有额度 {own} 且无可借→在线池软告警口径仍放行（B2=alarm 不阻断）")
    if pool not in ONLINE_POOLS and total >= budget.peak:  # 规则③
        return PoolVerdict(
            False, False, f"峰时单向借出（问答优先）：全局在飞 {total} ≥ 峰水位 {budget.peak}，离线池 {pool} 不得借占"
        )
    if strict and total >= budget.hard:  # 规则②
        return PoolVerdict(False, True, f"借占会突破全局硬上限 {budget.hard}（当前 {total}）→拒")
    return PoolVerdict(True, True, f"临时借出（自动归还）：出借方={lenders}，本次占第 {cur + 1} 个槽（全局 {total + 1}/{budget.hard}）")


class PoolLedger:
    """三本独立账的线程安全账本：进出记账 + 借占可观测，判定全在 `decide` 纯函数里。

    `report` 由调用方注入（组合根=B2 `inflight_llm` 上报口径），本模块不自判阈值、不 import providers。
    """

    def __init__(self, budget: PoolBudget | None = None, report: Callable[[int], None] | None = None) -> None:
        self._budget = budget or PoolBudget()
        self._report = report
        self._lock = threading.Lock()
        self._state: dict[str, int] = dict.fromkeys(ALL_POOLS, 0)

    @property
    def budget(self) -> PoolBudget:
        return self._budget

    def inflight(self, pool: str) -> int:
        return self._state.get(pool, 0)

    def total(self) -> int:
        return sum(self._state.values())

    def outstanding_borrow(self, pool: str) -> int:
        """④可观测面：该池当前占着的借来槽位数（=自动归还的待还窗口）。"""
        return max(0, self.inflight(pool) - self._budget.own[pool])

    def snapshot(self) -> dict[str, object]:
        """运维面读数（Admin 消费）：各池在飞/额度/借占 + 全局在飞与水位。"""
        return {
            "inflight": {p: self.inflight(p) for p in ALL_POOLS},
            "own": dict(self._budget.own),
            "borrowed": {p: self.outstanding_borrow(p) for p in ALL_POOLS},
            "total": self.total(),
            "hard": self._budget.hard,
            "peak": self._budget.peak,
            "at_peak": self.total() >= self._budget.peak,
        }

    def acquire(self, pool: str, *, strict: bool = True) -> Acquisition:
        with self._lock:
            v = decide(self._budget, self._state, pool, strict=strict)
            if v.granted:
                self._state[pool] = self._state.get(pool, 0) + 1
                self._fire()
            return Acquisition(v.granted, pool, v.borrowed, self._state[pool], sum(self._state.values()), v.reason)

    def release(self, pool: str) -> None:
        with self._lock:
            if pool not in self._state:
                raise ValueError(f"未知池：{pool}")
            self._state[pool] = max(0, self._state[pool] - 1)  # 归还=借占窗口随之收窄
            self._fire()

    @contextmanager
    def slot(self, pool: str, *, strict: bool = True) -> Iterator[Acquisition]:
        acq = self.acquire(pool, strict=strict)
        try:
            yield acq
        finally:
            if acq.granted:
                self.release(pool)

    def _fire(self) -> None:
        if self._report is not None:
            self._report(sum(self._state.values()))
