"""M4-x gateway Admin 运维面契约：U4 审计回放（按 kind/时间窗只读）+ §10 双人复位入口强制。

补齐 `AuditSink.read_all` / `PolicyGate.reset_breaker` 此前零 src 调用者的孤立缝——
§5 承诺"admin API/CLI（唯一对外接口）"的程序化落点：无 server 也可经 façade 查回放、复位恢复写侧。
断言口径正确性（过滤/门禁/恢复），非模型质量；复位仅合成既有 breaker_reset 审计，不新增 kind。
"""

from __future__ import annotations

import pytest
from src.partitions.memory.service.audit_sink import AuditSink
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.gateway.admin import Admin
from src.platform.gateway.policy_gate import PolicyGate
from src.platform.harness.breaker_rules.loader import load_rules
from src.platform.providers.errors import BreakerTripped
from src.platform.types import AuditEvent, ChangeEvent, Document


def _admin(tmp_path) -> Admin:
    audit = AuditSink(str(tmp_path / "audit"))
    gate = PolicyGate(load_rules(), audit, shadow_only=set())
    return Admin(audit, gate)


def test_replay_filters_by_kind(tmp_path) -> None:
    """无 server 亦可查：按 kind 只读回放，命中子集不含其它类型（U4 场景）。"""
    a = _admin(tmp_path)
    a._audit.write(AuditEvent(kind="breaker_trip", ts=1.0, detail="t1"))  # noqa: SLF001
    a._audit.write(AuditEvent(kind="acl_denied", ts=2.0, detail="d1"))  # noqa: SLF001
    a._audit.write(AuditEvent(kind="breaker_trip", ts=3.0, detail="t2"))  # noqa: SLF001
    trips = a.replay_audit(kinds={"breaker_trip"})
    assert [e.detail for e in trips] == ["t1", "t2"]
    assert all(e.kind == "breaker_trip" for e in trips)


def test_replay_filters_by_since_ts(tmp_path) -> None:
    """since_ts 为下界（含）：只回放窗口内事件。"""
    a = _admin(tmp_path)
    for ts in (1.0, 5.0, 9.0):
        a._audit.write(AuditEvent(kind="external_call", ts=ts, detail=f"e{ts}"))  # noqa: SLF001
    recent = a.replay_audit(since_ts=5.0)
    assert [e.ts for e in recent] == [5.0, 9.0]


def test_replay_no_filter_returns_all(tmp_path) -> None:
    a = _admin(tmp_path)
    a._audit.write(AuditEvent(kind="quarantine", ts=1.0))  # noqa: SLF001
    a._audit.write(AuditEvent(kind="wiki_publish", ts=2.0))  # noqa: SLF001
    assert len(a.replay_audit()) == 2


def test_reset_requires_two_distinct_signers(tmp_path) -> None:
    """双人复位门禁：空签署人 / 同一人重复签署均被拒（§10 B1 双人确认）。"""
    a = _admin(tmp_path)
    with pytest.raises(ValueError):
        a.reset_breaker("B1", operator="", approver="sre-b", reason="处置")
    with pytest.raises(ValueError):
        a.reset_breaker("B1", operator="sre-a", approver="sre-a", reason="处置")
    with pytest.raises(ValueError):
        a.reset_breaker("B1", operator="sre-a", approver="sre-b", reason="")


def test_reset_recovers_write_side(tmp_path) -> None:
    """端到端恢复：三次硬断拉 B0 冻结写侧→双人复位一条→写侧解冻、审计留 breaker_reset。"""
    a = _admin(tmp_path)
    for _ in range(3):
        with pytest.raises(BreakerTripped):
            a._gate.check("batch_delete_ratio", 0.5, "B1", partition="ingest")  # noqa: SLF001
    with pytest.raises(BreakerTripped):
        a._gate.ensure_write_allowed("ingest")  # noqa: SLF001
    a.reset_breaker("B1", operator="sre-a", approver="sre-b", reason="已切换本地供给")
    a._gate.ensure_write_allowed("ingest")  # noqa: SLF001  不再抛=写侧恢复
    resets = a.replay_audit(kinds={"breaker_reset"})
    assert resets and "+sre-b" in resets[-1].extra["by"]  # type: ignore[index]


def test_reset_needs_an_unresolved_trip_and_leaves_no_trace_when_refused(tmp_path) -> None:
    """复位无对象即拒且**不留痕**（M4-ct／审计 #9 顺带查出）：从未触发的熔断被"复位"曾返回 rc=0 并落一条
    `breaker_reset`＝把"没触发过"记成"已复位"，而 M4-cs 的启动回放会原样读回这条假账。拒绝必须发生在写账之前。
    """
    a = _admin(tmp_path)
    with pytest.raises(ValueError, match="复位无对象"):
        a.reset_breaker("B1", operator="sre-a", approver="sre-b", reason="照抄 runbook 的回执观察")
    assert a.replay_audit(kinds={"breaker_reset"}) == []
    assert a.replay_audit() == []  # 拒的就是"审计里干净"，不是"审计里多一条失败说明"


def test_reset_is_consumed_once_per_trip(tmp_path) -> None:
    """一条触发只可复位一次：二次复位＝第二次没有未复位对象，同样拒。"""
    a = _admin(tmp_path)
    with pytest.raises(BreakerTripped):
        a._gate.check("batch_delete_ratio", 0.5, "B1", partition="ingest")  # noqa: SLF001
    a.reset_breaker("B1", operator="sre-a", approver="sre-b", reason="处置完成")
    with pytest.raises(ValueError, match="复位无对象"):
        a.reset_breaker("B1", operator="sre-c", approver="sre-d", reason="重复点击")
    assert len(a.replay_audit(kinds={"breaker_reset"})) == 1


def test_alarm_and_observe_tiers_have_no_reset_object(tmp_path) -> None:
    """只告警（B2 soft）与观察档（B4 shadow / dev 默认 shadow 的 B1）进了审计却进不了台账→复位必须被拒：
    「双人复位」只对需人工处置的真实硬断存在，否则复位变成对无事的空签署。
    """
    a = _admin(tmp_path)
    assert a._gate.check("inflight_llm", 9, "B2", partition="platform").action == "alarm"  # noqa: SLF001
    assert a._gate.check("injection_score", 0.9, "B4", partition="platform").action == "observe"  # noqa: SLF001
    for breaker in ("B2", "B4"):
        with pytest.raises(ValueError, match="复位无对象"):
            a.reset_breaker(breaker, operator="sre-a", approver="sre-b", reason="处置")  # type: ignore[arg-type]
    assert a.replay_audit(kinds={"breaker_reset"}) == []

    shadowed = Admin(AuditSink(str(tmp_path / "audit2")), PolicyGate(load_rules(), AuditSink(str(tmp_path / "audit2")), shadow_only={"B1"}))
    assert shadowed._gate.check("batch_delete_ratio", 0.5, "B1").action == "observe"  # noqa: SLF001
    with pytest.raises(ValueError, match="复位无对象"):
        shadowed.reset_breaker("B1", operator="sre-a", approver="sre-b", reason="处置")


def test_container_exposes_admin(tmp_path) -> None:
    """组合根接线：build_container 产出的 .admin 可达，能回放真实操作产生的审计（含经管道触发的熔断）。"""
    c = build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), shadow_breakers=""))
    c.ingest.process_document(Document(doc_id="d0", version=1, domain="hr", acl_tags=["hr"], raw_text="# 政策\n\n" + "\n\n".join(f"第{i}段说明。" for i in range(30))))
    with pytest.raises(BreakerTripped):  # 危险批量删除被 B1 拦下并落 breaker_trip 审计
        c.ingest.apply_changes([ChangeEvent(doc_id=f"x{i}", kind="delete") for i in range(5)])
    assert isinstance(c.admin, Admin)
    trips = c.admin.replay_audit(kinds={"breaker_trip"})
    assert trips and all(e.kind == "breaker_trip" for e in trips)
