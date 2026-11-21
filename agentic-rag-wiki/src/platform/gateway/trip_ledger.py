"""B0 总闸触发史台账（M4-a）：累积未复位熔断触发，连续达阈即升级为总闸拉闸、冻结写侧、保读侧。

纯逻辑、零 IO（P1 等价内核）：升级判定走 `platform/breaker.evaluate_b0`；复位由双人按 runbook 处置后写入。
跨区三禁③呼应：本台账只记"触发/复位"事实，绝不自动改写安全参数阈值。
M4-cs（审计 #2／ADR-012）：台账可在启动时**从 append-only 审计事件回放**（`restore`）＝重启不再是"双人复位"
的旁路；写侧记账与读回共用 `counts_as_hard_trip` **一个**谓词，两处口径分叉就等于台账与账本分叉。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from src.platform.breaker import evaluate_b0
from src.platform.types import AuditEvent
from src.platform.types.breaker import BreakerDecision, BreakerId, TripRecord


def counts_as_hard_trip(event: AuditEvent) -> bool:
    """一条审计是否代表"需双人复位的真实硬断触发"——落账与回放共用这**一个**判据。

    判据取**事件形状**而非再读一遍规则：`PolicyGate.check` 写出的原始触发事件必带 `shadow` 键，
    其中 `shadow=False ∧ action=block` 才计（软告警与 shadow 观察不计，防 routine 容量告警误升总闸）。
    `ensure_write_allowed` 写的是 B0 **升级读数**（带 `escalated_from`、不带 `shadow`）＝台账的输出而非输入；
    把它读回成触发＝每次被冻结的写尝试都新增一条触发＝执法自增。
    """
    extra = event.extra
    return event.kind == "breaker_trip" and extra.get("shadow") is False and extra.get("action") == "block"


class TripLedger:
    def __init__(self, consecutive_trigger: int = 3) -> None:
        self._trips: list[TripRecord] = []
        self._n = consecutive_trigger

    @property
    def trips(self) -> list[TripRecord]:
        return list(self._trips)

    def record(self, breaker: BreakerId, *, ts: float, reason: str = "") -> None:
        self._trips.append(TripRecord(breaker=breaker, tripped_at=ts, reason=reason))

    def unresolved(self) -> list[TripRecord]:
        return [t for t in self._trips if t.reset_at is None]

    def reset(self, breaker: BreakerId, *, by: str, reason: str, ts: float) -> None:
        """双人复位最近一条该熔断的未复位触发：append-only 语义下只追加处置字段，不删改历史判定。"""
        for t in reversed(self._trips):
            if t.breaker == breaker and t.reset_at is None:
                t.reset_at = ts
                t.reset_by = by
                t.reset_reason = reason
                return

    def master(self, *, ts: float = 0.0) -> BreakerDecision:
        """当前未复位触发是否已达总闸升级阈（达阈→block=冻结写侧）。"""
        return evaluate_b0(self.unresolved(), consecutive_trigger=self._n, ts=ts)

    def restore(self, events: Iterable[AuditEvent]) -> int:
        """启动回放：按原序把审计账本里的"真实硬断触发/双人复位"重建进台账，返回重建的触发条数。

        三条纪律（审计 #2 的收口口径）：
        - **只在此进程尚未记过任何触发时回放**（非空即 fail-loud）＝同一条触发记两次会把升级计数虚高，
          而那正是"谁都被冻住、没人知道为什么"的形态；
        - **回放不落审计**（只读不传感，M4-ci 同一条纪律）＝重建动作不得自己成为事件源；
          解冻/冻结的传感仍只在真正那次写侧判门上发生一次；
        - **读不懂就拒起**：不可解析的事件行由 `AuditSink.read_all` 原样抛错、不吞不回退默认值——
          一本读不出来的安全台账不能当作"没有未复位触发"。
        """
        if self._trips:
            raise ValueError("触发台账非空，拒绝回放（回放只在组合根启动时执行一次）")
        restored = 0
        for event in events:
            if counts_as_hard_trip(event):
                # cast 只关掉静态层的 Literal 收窄，运行时仍由 `TripRecord` 校验成员资格：账本里冒出未知熔断号＝抛错
                self.record(cast("BreakerId", event.extra["breaker"]), ts=event.ts, reason=event.detail)
                restored += 1
            elif event.kind == "breaker_reset":
                breaker = event.extra.get("breaker")
                if isinstance(breaker, str):
                    by = event.extra.get("by")
                    self.reset(cast("BreakerId", breaker), by=str(by) if by else "", reason=event.detail, ts=event.ts)
        return restored
