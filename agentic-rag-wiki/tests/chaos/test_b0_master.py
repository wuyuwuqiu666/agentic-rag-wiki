"""M4-a B0 总闸成对演练（E7 硬门：缺一即 CI 红）。

分级双向断言：①未复位触发数 < 升级阈→只降级不断写（ensure_write_allowed 放行、读侧照常）；
②连续未复位达阈→拉总闸冻结**写侧**（保读侧）；③双人复位解除→写侧恢复。
"""

from __future__ import annotations

import pytest
from src.partitions.memory.service.audit_sink import AuditSink
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.gateway.policy_gate import PolicyGate
from src.platform.gateway.trip_ledger import TripLedger
from src.platform.harness.breaker_rules.loader import load_rules
from src.platform.providers.errors import BreakerTripped
from src.platform.types import ChangeEvent, Document

_TEXT = "# 政策\n\n" + "\n\n".join(f"第{i}段：执行档说明文字，长度足以形成多块。" for i in range(30))


def _gate(tmp_path) -> PolicyGate:
    s = Settings(audit_dir=str(tmp_path / "audit"))
    return PolicyGate(load_rules(), AuditSink(s.audit_dir), shadow_only=set())  # M4 全硬断：无 shadow


def test_ledger_paired_degrade_then_freeze_then_reset() -> None:
    """纯台账口径成对：2 条未复位→只降级不断写；3 条→拉闸；复位 1 条→解除。"""
    ledger = TripLedger()
    for i in range(2):
        ledger.record("B3", ts=float(i), reason="供给失败")
    assert not ledger.master().tripped  # 双触发未达阈：不冻结写侧（只降级不断写）

    ledger.record("B1", ts=3.0, reason="批量删除")
    d = ledger.master()
    assert d.tripped and d.action == "block" and set(d.escalated_from) == {"B3", "B1"}  # 三触发→拉闸

    ledger.reset("B3", by="sre-a", reason="已切换本地供给", ts=4.0)  # 双人复位一条
    ledger.reset("B3", by="sre-b", reason="复核确认", ts=5.0)
    assert not ledger.master().tripped  # 未复位降到 1 条 < 阈→写侧恢复


def test_gate_ensure_write_allowed_freezes_at_three(tmp_path) -> None:
    """运行期闸集成：三次硬断累积后 ensure_write_allowed 抛 B0（冻结全部写侧）。"""
    gate = _gate(tmp_path)
    for _ in range(3):
        with pytest.raises(BreakerTripped):  # 每次 B1 硬断先冻结当次操作并计入台账
            gate.check("batch_delete_ratio", 0.5, "B1", partition="ingest")
    assert len(gate._ledger.unresolved()) == 3  # noqa: SLF001 - 测试内检视累积触发
    with pytest.raises(BreakerTripped) as exc:
        gate.ensure_write_allowed("ingest")
    assert exc.value.decision.breaker == "B0"  # type: ignore[union-attr]
    gate.reset_breaker("B1", by="sre-a", reason="runbook 处置")  # 复位拉回阈下
    gate.ensure_write_allowed("ingest")  # 不再抛=写侧恢复


def test_pipeline_write_frozen_after_trips_read_still_served(tmp_path) -> None:
    """端到端执法点：B0 达阈后摄取写入口被冻结，但问答读侧不受影响（保读侧）。"""
    c = build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), shadow_breakers=""))
    c.ingest.process_document(Document(doc_id="d0", version=1, domain="hr", acl_tags=["hr"], raw_text=_TEXT))
    for _ in range(3):
        with pytest.raises(BreakerTripped):  # 三次危险批量删除各自被 B1 拦下并累积
            c.ingest.apply_changes([ChangeEvent(doc_id=f"x{i}", kind="delete") for i in range(5)])
    # 第 4 次即便内容是安全的（无删除）也被 B0 总闸在入口挡下=写侧冻结
    with pytest.raises(BreakerTripped) as exc:
        c.ingest.process_document(Document(doc_id="d1", version=1, domain="hr", acl_tags=["hr"], raw_text=_TEXT))
    assert exc.value.decision.breaker == "B0"  # type: ignore[union-attr]
    ans = c.app.answer("执行档说明", user_acl=["hr"])  # 读侧不受 B0 影响
    assert not ans.refused or ans.citations is not None
