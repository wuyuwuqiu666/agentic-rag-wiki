"""M4-cs（审计 #2／ADR-012）：B0 触发台账**启动回放**＝重启不是双人复位的旁路。

洞在哪（审计取证口径）：`TripLedger` 纯内存、组合根启动零恢复，于是"三次硬断→拉总闸→冻结写侧"
这条安全纪律被**重启**静默绕过——进程一换，未复位触发清零、写侧自动解冻，而 append-only 审计账本里
明明留着"有 trip 无 reset"的执法线索，系统从不读回。

判据一律走**真实执法面**（写侧被冻住＝摄取入口抛 B0），不靠翻台账内部字段；只在"执法自增"与
"复位配对次序"两条上直读台账——那两条正是读回口径自身的风险面。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from src.partitions.memory.service.audit_sink import AuditSink
from src.platform.config import Settings
from src.platform.gateway.composition import build_container
from src.platform.gateway.http import _create_app
from src.platform.gateway.policy_gate import PolicyGate
from src.platform.gateway.trip_ledger import TripLedger, counts_as_hard_trip
from src.platform.harness.breaker_rules.loader import load_rules
from src.platform.providers.errors import BreakerTripped
from src.platform.types import AuditEvent, ChangeEvent, Document

_TEXT = "# 政策\n\n" + "\n\n".join(f"第{i}段：执行档说明文字，长度足以形成多块。" for i in range(30))


def _hard_settings(tmp_path) -> Settings:
    return Settings(
        artifact_dir=str(tmp_path / "art"), audit_dir=str(tmp_path / "audit"), shadow_breakers=""
    )


def _audit_lines(audit_dir: str) -> list[str]:
    return [
        line
        for path in sorted(Path(audit_dir).glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _trip_three(s: Settings) -> None:
    """三次 B1 硬断（危险批量删除占比）：每次都各自被拦下并计入台账，达阈后写侧冻结。"""
    c = build_container(s)
    for i in range(3):
        with pytest.raises(BreakerTripped):
            c.ingest.apply_changes([ChangeEvent(doc_id=f"x{i}_{k}", kind="delete") for k in range(5)])


def test_restart_does_not_unfreeze_writes(tmp_path) -> None:
    """三次硬断 → 换进程（同审计目录重建容器）：写侧仍被 B0 冻住，读侧不受这道门影响。"""
    s = _hard_settings(tmp_path)
    _trip_three(s)

    c2 = build_container(s)
    with pytest.raises(BreakerTripped) as exc:
        c2.ingest.process_document(Document(doc_id="d1", version=1, domain="hr", acl_tags=["hr"], raw_text=_TEXT))
    assert exc.value.decision.breaker == "B0"  # type: ignore[union-attr]
    assert exc.value.decision.value == 3.0  # 读回的未复位触发数＝冻住那一刻的真实条数

    ans = c2.app.answer("执行档说明", user_acl=["hr"])  # 读侧不判这道门：不因回放而被挡
    assert ans.refused  # 如实登记：dev 两路索引仍是进程内 stand-in（重启即空＝放行项②）——冻的是写侧，不冻读侧


def test_two_person_reset_survives_restart(tmp_path) -> None:
    """复位也跨进程生效：双人复位打回阈下 → 换进程后写侧恢复（回放不是"一律冻住"）。"""
    s = _hard_settings(tmp_path)
    _trip_three(s)
    build_container(s).admin.reset_breaker("B1", operator="sre-a", approver="sre-b", reason="已按 runbook 处置")

    c3 = build_container(s)
    c3.ingest.process_document(Document(doc_id="d1", version=1, domain="hr", acl_tags=["hr"], raw_text=_TEXT))


def test_replay_is_read_only_and_sends_no_signal(tmp_path) -> None:
    """只读不传感（M4-ci 同一条纪律）：重建容器这一动作本身不落任何审计行。"""
    s = _hard_settings(tmp_path)
    _trip_three(s)
    before = _audit_lines(str(s.audit_dir))
    build_container(s)
    assert _audit_lines(str(s.audit_dir)) == before


def test_shadow_observation_is_not_replayed(tmp_path) -> None:
    """dev 默认观察档（shadow_breakers 缺省含 B1,B5）的触发不是硬断：重启后不得读回成冻结写侧。"""
    s = Settings(artifact_dir=str(tmp_path / "art"), audit_dir=str(tmp_path / "audit"))
    c = build_container(s)
    c.ingest.apply_changes([ChangeEvent(doc_id=f"x{k}", kind="delete") for k in range(5)])  # 观察档：记而不拦
    assert c.guard._ledger.unresolved() == []  # noqa: SLF001 - shadow 不入账，正是回放不误冻的前提
    build_container(s).ingest.process_document(
        Document(doc_id="d1", version=1, domain="hr", acl_tags=["hr"], raw_text=_TEXT)
    )


def test_unreadable_ledger_refuses_to_start(tmp_path) -> None:
    """账本读不懂＝拒起，绝不静默解冻（NFR-8 fail-loud：一本读不出来的安全台账不能当作"没有未复位触发"）。"""
    s = _hard_settings(tmp_path)
    AuditSink(str(s.audit_dir)).write(AuditEvent(kind="breaker_trip", detail="x", extra={"breaker": "B1"}))
    path = next(iter(Path(str(s.audit_dir)).glob("audit-*.jsonl")))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{这不是 json\n")
    with pytest.raises(json.JSONDecodeError):
        build_container(s)


def test_escalation_readouts_do_not_self_inflate(tmp_path) -> None:
    """执法自增防线：冻结期每次写侧被挡都追加一条 B0 升级读数，重启后不得被读成新的触发。"""
    s = _hard_settings(tmp_path)
    _trip_three(s)
    c = build_container(s)
    for k in range(5):
        with pytest.raises(BreakerTripped):
            c.ingest.process_document(
                Document(doc_id=f"d{k}", version=1, domain="hr", acl_tags=["hr"], raw_text=_TEXT)
            )
    assert TripLedger().restore(AuditSink(str(s.audit_dir)).read_all()) == 3  # 5 条升级读数 + 3 条真触发 → 只读回 3
    assert {t.breaker for t in c.guard._ledger.unresolved()} == {"B1"}  # noqa: SLF001 - 读回口径本身


def test_restore_rejects_non_empty_ledger() -> None:
    """同一条触发记两次＝升级计数虚高（谁都被冻住、没人知道为什么）：非空台账直接 fail-loud。"""
    ledger = TripLedger()
    ledger.record("B1", ts=1.0, reason="批量删除")
    trip = AuditEvent(kind="breaker_trip", ts=2.0, extra={"breaker": "B1", "action": "block", "shadow": False})
    with pytest.raises(ValueError, match="非空"):
        ledger.restore([trip])


def test_reset_resolves_the_latest_unresolved_trip() -> None:
    """复位配对次序与进程内一致：打在**最近一条**未复位触发上，余者仍计。"""
    events = [
        AuditEvent(kind="breaker_trip", ts=1.0, detail="供给失败", extra={"breaker": "B3", "action": "block", "shadow": False}),
        AuditEvent(kind="breaker_trip", ts=2.0, detail="供给失败", extra={"breaker": "B3", "action": "block", "shadow": False}),
        AuditEvent(kind="breaker_reset", ts=3.0, detail="已切本地", extra={"breaker": "B3", "by": "sre-a+sre-b"}),
        AuditEvent(kind="breaker_trip", ts=4.0, detail="批量删除", extra={"breaker": "B1", "action": "block", "shadow": False}),
    ]
    ledger = TripLedger()
    assert ledger.restore(events) == 3
    assert [(t.breaker, t.tripped_at) for t in ledger.unresolved()] == [("B3", 1.0), ("B1", 4.0)]  # 复位打在 ts=2.0 那条上
    assert ledger.master().tripped is False  # 2 条 < 升级阈 3：只降级不断写


def test_hard_trip_predicate_is_one_shared_caliber() -> None:
    """落账与回放共用一个谓词：原始触发（带 shadow 键）才计；软告警/观察/升级读数都不计。"""
    assert counts_as_hard_trip(AuditEvent(kind="breaker_trip", extra={"action": "block", "shadow": False}))
    assert not counts_as_hard_trip(AuditEvent(kind="breaker_trip", extra={"action": "alert", "shadow": False}))
    assert not counts_as_hard_trip(AuditEvent(kind="breaker_trip", extra={"action": "block", "shadow": True}))
    assert not counts_as_hard_trip(  # ensure_write_allowed 的 B0 升级读数：无 shadow 键
        AuditEvent(kind="breaker_trip", extra={"action": "block", "escalated_from": ["B1"]})
    )
    assert not counts_as_hard_trip(AuditEvent(kind="breaker_reset", extra={"action": "block", "shadow": False}))


def test_frozen_restart_still_serves_reads_and_declares_seed(tmp_path) -> None:
    """拉闸期重启：门面照样起、读侧在服务，`/api/health` 如实说演示语料没灌（skipped）而不是假装成功。

    这条是本刀的**代价面**：回放让"重启即解冻"不再成立，于是启动期的 seed（一次写侧动作）也会被拦——
    若让它抛错，整个门面起不来＝连"冻结写侧、保读侧"都兑现不了。seed 不是执法面，跳过它不削弱任何闸。
    """
    s = _hard_settings(tmp_path)
    _trip_three(s)
    client = TestClient(_create_app(build_container(s)))
    assert str(client.get("/api/health").json()["seed"]).startswith("skipped:")  # 如实回显，不静默
    r = client.post(
        "/api/ingest",
        json={"doc_id": "m4cs-http", "version": 1, "domain": "fin", "acl_tags": ["fin"], "raw_text": _TEXT},
    )
    assert r.status_code == 503 and "写侧已拉闸（B0）" in r.json()["detail"]
    assert client.get("/api/health").status_code == 200  # 读侧仍在服务


def test_gate_writes_the_shape_replay_reads(tmp_path) -> None:
    """闸写出的事件形状就是回放读的口径（写侧与读侧不分叉）：同一条事件既落账也可被读回。"""
    sink = AuditSink(str(tmp_path / "audit"))
    gate = PolicyGate(load_rules(), sink, shadow_only=set())
    for _ in range(2):
        with pytest.raises(BreakerTripped):
            gate.check("batch_delete_ratio", 0.5, "B1", partition="ingest")
    assert TripLedger().restore(sink.read_all()) == 2 == len(gate._ledger.unresolved())  # noqa: SLF001
