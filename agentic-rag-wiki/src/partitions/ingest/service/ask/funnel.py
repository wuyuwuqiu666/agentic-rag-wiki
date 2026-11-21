"""FR-CAP-1 消费漏斗层级归属 + 命中率**稳态口径**内核（§8.5；v0.7 数字承诺撤下、层与口径保留）。

执法点：问答链每产出一次答案即归属唯一制胜层（①语义缓存/②wiki 页/③单跳/④多跳）并记入滚动窗账本，
`gateway.Admin.funnel_stats` 读回——"≥30%/≥25% 企业档稳态目标"自此有**可机检的分子与分母**，不再是
只在文档里承诺、代码里无处测量的口径（同族缺陷=§15 E5 错误预算，M4-ae 已立判级内核）。

口径立场（写死在本模块，杜绝各消费方各自解释）：
- **分母=窗内有效请求**（漏斗四层任一产出答案）；拒答/ACL 全拦不入分母、另记 `refused`——拒答不是
  任何一层的"命中"，混入会稀释命中率并使跨窗/跨档数字不可比。
- **②层 dev 档归属口径**=`prefer_wiki` 同层重排后的**首引出处为 wiki 权威页**（`authority=="wiki"`）；
  不是独立短路路径。企业档若把②做成显式相似路由，归属规则不变。
- **浮点一律 `round(…, 9)`**：0.3 判不达 0.30 这类尾噪会让达标结论翻转（E5 同教训）。

诚实边界：执行档不承诺命中率数字（v0.7 撤下继承爬坡期条款）；真·逐窗采样作业 + 达标的运营判定须在
启用企业档当日喂真实流量分布（合成流量的命中率无业务意义）。本模块只提供**可调用、可单测、可审计的
决策计算核心**，不伪造调度器、不以"无样本"冒充"已达标"。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Literal, NamedTuple

Layer = Literal["cache", "wiki", "single_hop", "multi_hop"]
# 漏斗自上而下的四层键名（账本 counts/rates 的固定口径列，与 §8.5 FR-CAP-1 ①②③④ 同序）
LAYERS: tuple[str, ...] = ("cache", "wiki", "single_hop", "multi_hop")
_REFUSED = "refused"

# §8.5 FR-CAP-1 企业档稳态目标（执行档撤下承诺，但判级入参留在内核里，外推档启用当日直接复用）
STEADY_STATE_TARGETS: dict[str, float] = {"cache": 0.30, "wiki": 0.25}


def classify_layer(*, leading_authority: str, hops: int) -> Layer:
    """一条**已产出答案**（非缓存命中）的请求归属哪一层：②wiki 优先于③④，③④按跳数分。

    ①层在问答链入口即短路返回、不经检索，故由调用点直接记 `"cache"`，不在此判定。
    """
    if leading_authority == "wiki":
        return "wiki"
    return "multi_hop" if hops > 1 else "single_hop"


def rate(count: int, effective: int) -> float:
    """层命中率=层计数/有效请求数；分母 0 → 0.0（窗内无样本视为未命中，绝不虚构分母）。"""
    if effective <= 0:
        return 0.0
    return round(count / effective, 9)


@dataclass(frozen=True)
class FunnelSnapshot:
    """一次账本读数（窗口径由 `window_seconds` 声明，None=全量累计）。"""

    window_seconds: float | None
    effective: int
    refused: int
    counts: dict[str, int]
    rates: dict[str, float]


class Assessment(NamedTuple):
    """稳态目标达标判定；`evaluated=False`=窗内零有效样本，此时不产出任何达标结论。"""

    evaluated: bool
    unmet: tuple[str, ...]
    rates: dict[str, float]
    targets: dict[str, float]


def assess(snapshot: FunnelSnapshot, targets: dict[str, float] | None = None) -> Assessment:
    goals = dict(STEADY_STATE_TARGETS if targets is None else targets)
    if snapshot.effective <= 0:
        return Assessment(evaluated=False, unmet=(), rates={}, targets=goals)
    unmet = tuple(sorted(layer for layer, goal in goals.items() if snapshot.rates[layer] < goal))
    return Assessment(
        evaluated=True, unmet=unmet, rates={layer: snapshot.rates[layer] for layer in sorted(goals)}, targets=goals
    )


class FunnelLedger:
    """四层归属 + 拒答的滚动窗账本（线程安全；问答出口天然并发）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[tuple[float, str]] = []

    def record(self, layer: Layer, *, ts: float | None = None) -> None:
        self._append(layer, time.time() if ts is None else ts)

    def record_refused(self, *, ts: float | None = None) -> None:
        self._append(_REFUSED, time.time() if ts is None else ts)

    def _append(self, kind: str, ts: float) -> None:
        with self._lock:
            self._events.append((ts, kind))

    def snapshot(self, *, window_seconds: float | None = None, now: float | None = None) -> FunnelSnapshot:
        """窗内读数：`window_seconds` 给定时取 `[now-window, now]` 下界含的事件；`now` 缺省用当前时刻。"""
        with self._lock:
            events = list(self._events)
        reference = time.time() if now is None else now
        picked = events if window_seconds is None else [ev for ev in events if ev[0] >= reference - window_seconds]
        counts = {layer: sum(1 for _, kind in picked if kind == layer) for layer in LAYERS}
        effective = sum(counts.values())
        return FunnelSnapshot(
            window_seconds=window_seconds,
            effective=effective,
            refused=sum(1 for _, kind in picked if kind == _REFUSED),
            counts=counts,
            rates={layer: rate(counts[layer], effective) for layer in LAYERS},
        )
