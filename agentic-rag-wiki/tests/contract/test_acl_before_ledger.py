"""M4-cq（审计 #3）：acl_change 的**前值账**与"恢复/补偿不回落旧受众"。

洞在哪（取证口径，非臆测）：`_acl_rewrite` 的目标集只取 A 区清单，文档一经下架（清单 `forget_version`）
收紧就静默 no-op；而删除墓碑存的是**下架那一刻**的受众信封，`restore_deletes` 按它重放＝把收紧前的宽受众
重新灌回两路索引。正文指针缺失那条支同族：acl 计入 `acl_pending` 未落清单，之后 `compensate()` 重放照样回落。

判据一律走**检索结局**（与 `test_acl_rewrite.py` 同口径）：任何一条路径把旧域带回来，旧域读者就能拿到引用。
前值账（从哪个域收到哪个域）本身也走读回面核，不靠翻 provider 内部。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.gateway.cli import main
from src.platform.gateway.http import _create_app
from src.platform.types import ChangeEvent, Document

_TEXT = "# 会议纪要归档\n\n" + "\n\n".join(f"第{i}条：会议决议与后续跟进事项的说明文字。" for i in range(40))


def _ctr(tmp_path) -> object:
    return build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))  # type: ignore[arg-type]


def _retire(c, doc_id: str) -> None:
    c.ingest.apply_changes([ChangeEvent(doc_id=doc_id, kind="delete", version=1)])


def test_tightening_applied_while_retired_survives_restore(tmp_path) -> None:
    """下架 → 收紧 → 恢复：恢复出来的块必须按**最新**受众供答，旧域读者不得拿回引用。"""
    c = _ctr(tmp_path)
    c.ingest.process_document(
        Document(doc_id="meet", version=1, domain="fin", doc_type="meeting", acl_tags=["fin"], raw_text=_TEXT)
    )
    assert c.app.answer("会议决议跟进事项", user_acl=["fin"]).citations
    _retire(c, "meet")
    assert c.app.answer("会议决议跟进事项", user_acl=["fin"]).refused

    stats = c.ingest.apply_changes(
        [ChangeEvent(doc_id="meet", kind="acl_change", version=1, acl_tags_before=["fin"], acl_tags_after=["hr"])]
    )
    assert stats["acl_retired"] == 1  # 收紧落在下架件上不再静默：如实报"改的是账，不是索引"
    assert stats["acl_rewritten"] == 0 and stats["acl_pending"] == 0

    out = c.ingest.restore_deletes(doc_id="meet")
    assert out["restored"] == 1
    assert c.app.answer("会议决议跟进事项", user_acl=["fin"]).refused  # 回落旧受众＝权限收紧被撤销
    assert c.app.answer("会议决议跟进事项", user_acl=["hr"]).citations


def test_retired_tightening_records_real_before_and_chains(tmp_path) -> None:
    """下架件的前值**取实值不采信请求自述**，且连续两次收紧要在账上接成链（第二次前值＝第一次结果）。

    真 HTTP 核验抓出的口径分叉：在册版走清单信封取前值，下架版最初实现直接抄请求里的 `acl_before`——
    调用方报个假前值就能把受众变更史写歪（"从 ops 收到 hr"），与文档承诺的"取实值"两张皮。
    """
    c = _ctr(tmp_path)
    c.ingest.process_document(
        Document(doc_id="meet", version=1, domain="fin", doc_type="meeting",
                 acl_tags=["fin", "hr"], raw_text=_TEXT)
    )
    _retire(c, "meet")
    c.ingest.apply_changes(
        [ChangeEvent(doc_id="meet", kind="acl_change", version=1,
                     acl_tags_before=["ops"], acl_tags_after=["hr"])]  # 假前值：账不认
    )
    row = c.ingest.acl_history("meet")[0]
    assert row["before"] == ["fin", "hr"]  # 实值＝墓碑记录的"下架那一刻"受众
    assert row["after"] == ["hr"] and row["applied_to"] == "retired"

    c.ingest.apply_changes(
        [ChangeEvent(doc_id="meet", kind="acl_change", version=1,
                     acl_tags_before=["fin"], acl_tags_after=["legal"])]
    )
    rows = c.ingest.acl_history("meet")
    assert [r["before"] for r in rows] == [["fin", "hr"], ["hr"]]  # 链：第二次前值＝第一次的 to
    assert rows[-1]["after"] == ["legal"]

    c.ingest.restore_deletes(doc_id="meet")
    assert c.app.answer("会议决议跟进事项", user_acl=["legal"]).citations
    assert c.app.answer("会议决议跟进事项", user_acl=["fin"]).refused
    assert c.app.answer("会议决议跟进事项", user_acl=["hr"]).refused


def test_acl_history_keeps_before_and_after_per_version(tmp_path) -> None:
    """前值账的可读回面：一条收紧留下 from→to 的痕，运维能问"这页凭什么从 fin 收到 hr"。"""
    c = _ctr(tmp_path)
    c.ingest.process_document(
        Document(doc_id="meet", version=1, domain="fin", doc_type="meeting", acl_tags=["fin", "hr"], raw_text=_TEXT)
    )
    c.ingest.apply_changes(
        [ChangeEvent(doc_id="meet", kind="acl_change", version=1, acl_tags_before=["fin", "hr"], acl_tags_after=["hr"])]
    )
    rows = c.ingest.acl_history("meet")
    assert len(rows) == 1
    row = rows[0]
    assert (row["doc_id"], row["version"]) == ("meet", 1)
    assert row["before"] == ["fin", "hr"] and row["after"] == ["hr"]
    assert row["applied_to"] == "inventory"  # 落点分档：在册块改写 / 下架件改账 / 正文缺失待补


def test_unknown_doc_records_nothing(tmp_path) -> None:
    """清单与墓碑都不认的 doc＝无从收紧：不造记录、不声称任何效果（防把幻影条目写进账）。"""
    c = _ctr(tmp_path)
    stats = c.ingest.apply_changes([ChangeEvent(doc_id="ghost", kind="acl_change", acl_tags_after=["hr"])])
    assert stats["acl_rewritten"] == 0 and stats["acl_pending"] == 0 and stats["acl_retired"] == 0
    assert c.ingest.acl_history("ghost") == []


def test_acl_history_is_one_ledger_three_surfaces_and_pure_read(tmp_path, capsys) -> None:
    """前值账的三个面（管线 / `GET /api/ingest/acl-history` / CLI `admin acl-history`）读同一本账，且读回纯读。

    只读不传感（承 M4-ci 的读侧纪律）：运维刷新看账不得自己变成故障源；读回也不动索引、不改受众。
    """
    c = _ctr(tmp_path)
    c.ingest.process_document(
        Document(doc_id="meet", version=1, domain="fin", doc_type="meeting", acl_tags=["fin"], raw_text=_TEXT)
    )
    c.ingest.apply_changes(
        [ChangeEvent(doc_id="meet", kind="acl_change", version=1, acl_tags_before=["fin"], acl_tags_after=["hr"])]
    )
    client = TestClient(_create_app(c))
    ledger = c.ingest.acl_history("meet")
    http_body = client.get("/api/ingest/acl-history", params={"doc_id": "meet"}).json()
    assert main(["admin", "acl-history", "meet"], container=c) == 0
    cli_body = json.loads(capsys.readouterr().out)
    assert http_body["entries"] == ledger == cli_body["entries"]  # 口径分叉＝运维看到的账和真账不一致
    assert http_body["entries"][0]["after"] == ["hr"]

    ids_before = set(c.vectorstore.all_ids())
    again = client.get("/api/ingest/acl-history", params={"doc_id": "meet"}).json()
    assert again["entries"] == ledger  # 刷新不改账
    assert set(c.vectorstore.all_ids()) == ids_before  # 读回不动索引
    assert [e for e in c.audit.read_all() if e.kind == "breaker_trip"] == []  # 零传感


def test_acl_history_rejects_missing_doc_id_and_claims_nothing_for_unknown(tmp_path) -> None:
    """`doc_id` 是必填查询位（缺失 422，不猜"全量历史"）；未知文档＝空列表且不声称改过任何东西。"""
    c = _ctr(tmp_path)
    client = TestClient(_create_app(c))
    assert client.get("/api/ingest/acl-history").status_code == 422
    body = client.get("/api/ingest/acl-history", params={"doc_id": "ghost"}).json()
    assert body["entries"] == []
    assert "从未被" in body["hint"]
    for forbidden in ("已重写", "已收紧", "已修复"):
        assert forbidden not in body["hint"]


def test_change_receipt_reports_where_the_tightening_landed(tmp_path) -> None:
    """写侧回执与账同口径：下架件上的收紧不再静默——`retired_acl_docs` 计数＋hint 明写"恢复即按新域"。"""
    c = _ctr(tmp_path)
    c.ingest.process_document(
        Document(doc_id="meet", version=1, domain="fin", doc_type="meeting", acl_tags=["fin"], raw_text=_TEXT)
    )
    client = TestClient(_create_app(c))
    base = {"doc_id": "meet", "version": 1, "actor": "ops", "confirm": True}
    assert client.post("/api/ingest/change", json=base | {"kind": "delete"}).status_code == 200
    r = client.post(
        "/api/ingest/change", json=base | {"kind": "acl_change", "acl_before": ["fin"], "acl_after": ["hr"]}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["retired_acl_docs"] == 1 and body["acl_retired"] == 1
    assert any("前值已入账" in e for e in body["effects_done"])
    assert "不会回落旧受众" in body["hint"]
def test_before_value_taken_from_inventory_not_from_the_request(tmp_path) -> None:
    """前值取清单实值，不采信调用方自述：请求报个假 `acl_tags_before`（此处为空）也写不歪账。"""
    c = _ctr(tmp_path)
    c.ingest.process_document(
        Document(doc_id="meet", version=1, domain="fin", doc_type="meeting", acl_tags=["fin", "hr"], raw_text=_TEXT)
    )
    c.ingest.apply_changes([ChangeEvent(doc_id="meet", kind="acl_change", version=1, acl_tags_after=["hr"])])
    row = c.ingest.acl_history("meet")[0]
    assert row["before"] == ["fin", "hr"]  # 清单里的真前值，而非请求里缺失的自述
    assert row["after"] == ["hr"]


def test_pending_tightening_is_used_when_body_returns(tmp_path) -> None:
    """正文指针缺失时收紧只入账（pending），补回正文后 `compensate()` 重放必须按最新域，不回落清单旧域。"""
    c = _ctr(tmp_path)
    event = c.ingest.process_document(
        Document(doc_id="meet", version=1, domain="fin", doc_type="meeting", acl_tags=["fin"], raw_text=_TEXT)
    )
    body = Path(tmp_path) / "canonical-draft" / event.store_key
    saved = body.read_bytes()
    body.unlink()  # 清单在、正文丢

    stats = c.ingest.apply_changes(
        [ChangeEvent(doc_id="meet", kind="acl_change", version=1, acl_tags_before=["fin"], acl_tags_after=["hr"])]
    )
    assert stats["acl_pending"] > 0 and stats["acl_rewritten"] == 0
    assert c.ingest.acl_history("meet")[0]["applied_to"] == "pending_body"

    body.write_bytes(saved)  # 正文回来了
    # 造真背离：只掉正文不会让块从索引消失，需再从索引摘净，补偿才有活可干（missing>0）
    c.vectorstore.delete(sorted(c.vectorstore.all_ids()))
    rec = c.ingest.compensate(write_report=False)
    assert rec["backfilled"] == 1
    assert c.app.answer("会议决议跟进事项", user_acl=["fin"]).refused
    assert c.app.answer("会议决议跟进事项", user_acl=["hr"]).citations
