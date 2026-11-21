"""在飞 LLM 请求计数器（FR-CAP-3 / B2）：问答出口的并发预算，越阈交熔断闸判软告警。

线程安全的进出记账：进入即 +1 并上报 `inflight_llm` 当前值给 `GuardPort`（B2=soft，越阈只 alarm 不 block），
离开（含异常）即 -1。B2 的阈值/档位属 `harness/breaker_rules/b2.yaml`（禁改区），本模块不自判阈值。

分池预算（§8.5 v0.6）：注入 `PoolLedger` 后计数落在三本独立账上（本 tracker=问答在线池视图），
按 `strict=False` 取槽=B2 软告警口径**绝不因预算拒在线请求**，离线池借占则由账本按峰水位单向执法；
上报给 B2 的仍是**全局在飞**（阈值口径不因分池而变）。未注入 ledger=保持原单池全局计数（既有 dev 档行为）。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from src.platform.breaker.pool import PoolId, PoolLedger
from src.platform.providers import GuardPort


class InflightTracker:
    def __init__(
        self,
        guard: GuardPort | None = None,
        *,
        metric: str = "inflight_llm",
        breaker: str = "B2",
        ledger: PoolLedger | None = None,
        pool: PoolId = "qa",
    ) -> None:
        self._guard = guard
        self._metric = metric
        self._breaker = breaker
        self._ledger = ledger
        self._pool: PoolId = pool
        self._lock = threading.Lock()
        self._n = 0

    @property
    def count(self) -> int:
        return self._ledger.total() if self._ledger is not None else self._n

    def acquire(self) -> int:
        if self._ledger is not None:
            self._ledger.acquire(self._pool, strict=False)
            cur = self._ledger.total()
        else:
            with self._lock:
                self._n += 1
                cur = self._n
        if self._guard is not None:
            self._guard.check(self._metric, float(cur), self._breaker, partition="gateway")  # soft→alarm，不抛
        return cur

    def release(self) -> None:
        if self._ledger is not None:
            self._ledger.release(self._pool)  # 归还=借占窗口随之收窄（④自动归还）
            return
        with self._lock:
            self._n = max(0, self._n - 1)

    @contextmanager
    def slot(self) -> Iterator[object]:
        cur = self.acquire()
        try:
            yield cur
        finally:
            self.release()
