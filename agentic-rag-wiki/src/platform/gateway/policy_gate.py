"""运行期熔断闸（组合根构造）：规则来自 harness/breaker_rules，判定走 platform/breaker 纯内核。

分区经 `GuardPort` 交可疑指标，本闸做四件事：①按 (breaker,metric) 取规则并判定；
②M1 期 shadow 档（B1/B5 只记不拦，治复位疲劳、观察 2 周再硬断）；③触发即落 C 区审计，硬断则抛 BreakerTripped 冻结当前写侧；
④M4-a：真实硬断（action=block、需双人复位）计入 `TripLedger`，连续未复位达阈→`ensure_write_allowed` 升 B0 总闸冻结全部写侧（保读侧，复位解除；软告警/shadow 不计，防 routine 容量告警误升总闸）；
⑤M4-cs（审计 #2／ADR-012）：`restore_from_audit` 在组合根启动时从 append-only 审计账本回放未复位硬断——
换进程不再是"双人复位"的旁路；落账与回放共用 `counts_as_hard_trip` 一个谓词，B0 升级读数不回灌（防执法自增）。
"""

from __future__ import annotations

import time

from src.partitions.memory.service.audit_sink import AuditSink
from src.platform.breaker import evaluate
from src.platform.breaker.rules import Rule
from src.platform.gateway.trip_ledger import TripLedger, counts_as_hard_trip
from src.platform.providers.errors import BreakerTripped
from src.platform.types import AuditEvent, BreakerDecision
from src.platform.types.breaker import BreakerId, BreakerLevel


class PolicyGate:
    def __init__(self, rules: list[Rule], audit: AuditSink, shadow_only: set[str] | None = None) -> None:
        self._index: dict[tuple[str, str], Rule] = {(r.breaker, r.metric): r for r in rules}
        self._audit = audit
        self._shadow = shadow_only or set()
        self._ledger = TripLedger()

    def check(self, metric: str, value: float, breaker: str, partition: str = "platform") -> BreakerDecision:
        rule = self._index.get((breaker, metric))
        if rule is None:
            return BreakerDecision(breaker=breaker, tripped=False, metric=metric, value=value)  # type: ignore[arg-type]

        effective = rule
        shadowed = breaker in self._shadow
        if shadowed:
            effective = rule.model_copy(update={"level": BreakerLevel.SHADOW})

        decision = evaluate(metric, value, effective, ts=time.time())
        if decision.tripped:
            event = AuditEvent(
                kind="breaker_trip",
                partition=partition,
                detail=decision.reason,
                extra={"breaker": decision.breaker, "action": decision.action, "value": value, "shadow": shadowed},
            )
            # 仅"需双人复位的硬断"计入 B0 台账；软告警(B2 自愈)与 shadow 观察不计，否则 routine 容量告警会误升总闸。
            # M4-cs：判据用**这条事件本身**（`counts_as_hard_trip`），启动回放读的也是它＝落账集与读回集同源、不分叉。
            if counts_as_hard_trip(event):
                self._ledger.record(decision.breaker, ts=time.time(), reason=decision.reason)
            self._audit.write(event)
            if decision.action == "block":
                raise BreakerTripped(decision)
        return decision

    def restore_from_audit(self) -> int:
        """启动回放（M4-cs／审计 #2、ADR-012）：从 append-only 审计账本重建未复位硬断＝重启不再是复位旁路。

        由组合根在建容器时调用一次；只读账本、不写审计、不改阈值（跨区三禁③）。返回重建的触发条数，
        调用方不据此判定——判定仍只走 `ensure_write_allowed`→`evaluate_b0` 一条路。
        """
        return self._ledger.restore(self._audit.read_all())

    def ensure_write_allowed(self, partition: str = "platform") -> BreakerDecision:
        """写侧入口预检：未复位硬断累积达 B0 升级阈→拉总闸冻结写侧（读侧不受此门影响）。"""
        decision = self._ledger.master(ts=time.time())
        if decision.tripped:
            self._audit.write(
                AuditEvent(
                    kind="breaker_trip",
                    partition=partition,
                    detail=decision.reason,
                    extra={"breaker": decision.breaker, "action": decision.action, "value": decision.value, "escalated_from": decision.escalated_from},
                )
            )
            raise BreakerTripped(decision)
        return decision

    def reset_breaker(self, breaker: BreakerId, *, by: str, reason: str) -> None:
        """双人复位一条未复位触发（runbook 处置后），把连续计数打回阈值下、解除 B0。

        **无复位对象即拒**（NFR-8）：观察档（shadow）与只告警的 B2/B4 从不进触发台账，"复位"它们没有任何
        东西可解；照旧落一条 `breaker_reset` ＝把"没触发过"记成"已复位"，双人签署的追责链从此有假条目
        （M4-cs 的启动回放会原样读到它）。判前置于写账之前：拒绝的动作不留痕。
        """
        if not any(t.breaker == breaker and t.reset_at is None for t in self._ledger.trips):
            raise ValueError(f"{breaker} 没有未复位的硬断触发，复位无对象（勿把'没触发'记成'已复位'）")
        self._ledger.reset(breaker, by=by, reason=reason, ts=time.time())
        self._audit.write(
            AuditEvent(kind="breaker_reset", partition="platform", detail=reason, extra={"breaker": breaker, "by": by})
        )

