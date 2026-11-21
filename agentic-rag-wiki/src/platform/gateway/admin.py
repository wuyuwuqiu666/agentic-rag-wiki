"""运维/审计管理面（§5 gateway "admin API/CLI" 唯一对外接口的执行档程序化落点）。

分区侧只见 `GuardPort`（判定）/`AuditPort`（写审计）；运维侧的**读回与复位**经此 façade——
`gateway` 有权限触达分区实现（§6.1 组合根例外，非分区 import C 区），把 `AuditSink.read_all`
与 `PolicyGate.reset_breaker` 接成可达入口（U4 审计回放、§8.5/§10 双人复位解除 B0）。
只读回放不破 append-only；复位仅合成既有 `breaker_reset` 审计，不新增 kind、不动冻结契约。
"""

from __future__ import annotations

from src.partitions.ingest.service.ask.funnel import FunnelLedger, assess
from src.partitions.memory.service.audit_sink import AuditSink
from src.platform.breaker import slo
from src.platform.breaker.pool import PoolLedger
from src.platform.gateway.policy_gate import PolicyGate
from src.platform.types import AuditEvent
from src.platform.types.breaker import BreakerId


class Admin:
    def __init__(
        self,
        audit: AuditSink,
        gate: PolicyGate,
        pools: PoolLedger | None = None,
        funnel_ledger: FunnelLedger | None = None,
    ) -> None:
        self._audit = audit
        self._gate = gate
        self._pools = pools
        self._funnel = funnel_ledger

    def pool_budget(self) -> dict[str, object]:
        """§8.5 B2 分池预算运维面：三本独立账的在飞/额度/借占读数 + 是否处峰。

        `borrowed` 非零=临时借出窗口（按规则④随借方调用跑完自动归还，无须人工调额）；
        `at_peak`=离线池此刻再借将被拒（问答优先，防饿死在线流量）。未装配账本→空读数（不假造数据）。
        """
        return self._pools.snapshot() if self._pools is not None else {}

    def replay_audit(self, kinds: set[str] | None = None, since_ts: float | None = None) -> list[AuditEvent]:
        """U4：按事件类型 + 起始时间回放审计（只读，append-only 不受影响）。

        kinds 为 None 时返回全量；`since_ts` 为下界（含）。用于"查询上周所有熔断触发/越权被拒"。
        """
        events = self._audit.read_all()
        if kinds is not None:
            events = [e for e in events if e.kind in kinds]
        if since_ts is not None:
            events = [e for e in events if e.ts >= since_ts]
        return events

    def reset_breaker(self, breaker: BreakerId, *, operator: str, approver: str, reason: str) -> None:
        """§10/§8.5：双人复位一条未复位硬断（两名不同签署人 + 必填理由），解除累积、恢复写侧。

        真·跨人授权审批流属 E9 治理/企业档放行，本面先在入口结构化强制"两签署人相异且非空 + 理由非空"，
        并委派既有 `PolicyGate.reset_breaker` 落 `breaker_reset` 审计（by=operator+approver 留双签痕）。
        """
        if not operator or not approver:
            raise ValueError("双人复位须两名操作者签署，签署人不得为空")
        if operator == approver:
            raise ValueError("双人复位须由两名不同操作者签署（§10 B1 双人确认）")
        if not reason:
            raise ValueError("复位必填理由（R9 入审计）")
        self._gate.reset_breaker(breaker, by=f"{operator}+{approver}", reason=reason)

    def degrade_plan(self, *, total_requests: int, bad_requests: int, slo_target: float = 0.995) -> slo.DegradePlan:
        """§15 E5 运维面：给定窗口可用性计数，确定性返回错误预算透支判级与降级方案。

        NFR-1 查询面 99.5% 落成 SLO，E5"错误预算耗尽自动降级非关键功能"沿 FR-CAP-2 ④→③→② 阶梯逐级降。
        本面提供**可调用、可审计、可单测的决策计算核心**（杜绝"声称自动降级却无任何可决口径"）；
        真·逐日 SLO 计算作业 + 实时可用性喂数 + read 路径据 plan 自动降档执行=运行时放行档（需真实流量），
        不在此伪造调度器、不谎称闭环已自动降档。外部计数经 `ErrorBudget` 构造期校验（T2）。
        """
        return slo.assess(target=slo_target, total=total_requests, bad=bad_requests)

    def funnel_stats(self, *, window_seconds: float | None = None, now: float | None = None) -> dict[str, object]:
        """§8.5 FR-CAP-1 运维面：读回消费漏斗四层命中账本（滚动窗/全量）+ 企业档稳态目标判定。

        分子=层命中、分母=有效请求（拒答不入分母、另记 `refused`），口径全部由 `service/ask/funnel` 内核决出。
        执行档不承诺命中率数字（v0.7 撤下、继承爬坡期条款），本面提供**可调用、可单测、可审计的测量与判级核心**；
        真·逐窗采样作业与运营达标须企业档启用当日喂真实流量——不以合成流量或零样本冒充爬坡结论
        （`evaluated=False` 即"无样本不判定"，绝不谎称已达标）。
        """
        if self._funnel is None:
            return {}
        snap = self._funnel.snapshot(window_seconds=window_seconds, now=now)
        verdict = assess(snap)
        return {
            "window_seconds": snap.window_seconds,
            "effective": snap.effective,
            "refused": snap.refused,
            "counts": dict(snap.counts),
            "rates": dict(snap.rates),
            "evaluated": verdict.evaluated,
            "unmet": list(verdict.unmet),
            "targets": dict(verdict.targets),
        }
