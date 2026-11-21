"""M4-cr（审计 #17 补录）：变更批的**逐条账**——一条失败不吞掉整批的账，正文取不到不臆造重放。

洞在哪（取证口径，探针 `VERDICT` 实录，非臆测）：
① `apply_changes` 三个循环里任何一条抛错＝整批直接上抛、**回执为零**，而前面几条的效果（两路索引已清、
   清单已改账、墓碑已记、fan-out 已广播）**已经落地**——调用方只拿到一句异常文本，事后无从知道"哪几条已生效"，
   这正是本档反复拦的"部分效果无账"（对照 M4-cj"503 且零部分效果"、M4-cp"冻结＝推迟不消耗待办"）。
② upsert 分支把"正文不在 objectstore"当作 `b""` 重放入库＝**把内容清零伪装成更新**：回执照样记 `reindexed=1`，
   并向 B 区广播一条并不存在的 upsert（`_reindex_event` 早有"正文不可拉→False 不臆造"的口径，此处却另写一套＝
   同一事实两种算法）。
③ `domain_move` 不在三个循环里＝从账面凭空消失（批里 4 条、回执只见 3 条的账对不上）。

判据一律走**回执与检索/清单结局**，不翻 provider 内部计数（`_mq`/`_objects` 只在"证明没发生"时读）。
逐条明细（`items`）只随异常带出＝无失败批的回执保持 int-only（`pull`/`/api/ingest/dir` 形状不变），
故"被拒的原因"在**有失败的批**里可指（第 6 条用例），无失败批只给计数——可达门面对此各自的计数行已如实。
"""

from __future__ import annotations

import pytest
from src.partitions.ingest.runtime.pipeline import ChangeApplyError
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.types import ChangeEvent, Document

_TEXT = "# 变更记账语料\n\n" + "\n\n".join(f"第{i}条：逐条结果与可指原因的说明文字。" for i in range(30))


def _ctr(tmp_path):
    return build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))


def _ingest(c, doc_id: str) -> None:
    c.ingest.process_document(
        Document(doc_id=doc_id, version=1, domain="fin", doc_type="meeting", acl_tags=["fin"], raw_text=_TEXT)
    )


def _fail_on(c, doc_id: str) -> None:
    """把墓碑落账钉成"该 doc 必抛"——模拟真实落账故障（批内一条失败的注入位）。"""
    orig = c.ingest._delete_ledger.record

    def boom(ev, cids, *, deleted_at):
        if ev.doc_id == doc_id:
            raise RuntimeError(f"墓碑账写不进（模拟落账故障：{doc_id}）")
        return orig(ev, cids, deleted_at=deleted_at)

    c.ingest._delete_ledger.record = boom  # type: ignore[method-assign]


def test_missing_body_upsert_is_blocked_not_replayed(tmp_path) -> None:
    """正文从未入库 ⇒ `blocked` 且零效果：不记 reindexed、不向 B 区广播并不存在的更新、不新增清单条目。"""
    c = _ctr(tmp_path)
    _ingest(c, "real")
    depth_before = c.ingest._mq.depth("canonical.fanout")
    stats = c.ingest.apply_changes([ChangeEvent(doc_id="never", kind="upsert", version=1, acl_tags_after=["fin"])])

    assert stats["reindexed"] == 0, "正文从未存在却被记成『已重放』＝回执长得像成功"
    assert stats["blocked"] == 1 and stats["applied"] == 0 and stats["failed"] == 0
    assert c.ingest._mq.depth("canonical.fanout") == depth_before, "空正文重放仍广播 upsert＝B 区凭空气到一条变更"
    assert not c.ingest.is_indexed("never")
    assert c.ingest.reconcile(sense=False)["extra"] == 0, "被拒的更新不该在 B5 账上留下幻影块"


def test_blank_body_upsert_is_blocked(tmp_path) -> None:
    """正文存在但全空白 ⇒ 同样 `blocked`：空正文重放＝把内容清零伪装成更新，清零该走 kind=delete。"""
    c = _ctr(tmp_path)
    c.ingest._objects.put("blank/1", b"   \n  ")
    depth_before = c.ingest._mq.depth("canonical.fanout")
    stats = c.ingest.apply_changes([ChangeEvent(doc_id="blank", kind="upsert", version=1, acl_tags_after=["fin"])])

    assert stats["reindexed"] == 0 and stats["blocked"] == 1 and stats["applied"] == 0
    assert c.ingest._mq.depth("canonical.fanout") == depth_before
    assert not c.ingest.is_indexed("blank")


def test_real_body_upsert_still_replays(tmp_path) -> None:
    """反向半边（防"见 blocked 就一刀切拒收"）：正文在册且非空 ⇒ 照常重放入库、记 applied。"""
    c = _ctr(tmp_path)
    _ingest(c, "src")
    stats = c.ingest.apply_changes([ChangeEvent(doc_id="src", kind="upsert", version=1, acl_tags_after=["fin"])])

    assert stats["reindexed"] == 1 and stats["applied"] == 1 and stats["blocked"] == 0
    assert c.ingest.is_indexed("src")


def test_batch_failure_still_carries_the_per_item_ledger(tmp_path) -> None:
    """批内第 2 条抛错：其余条照跑，异常**带账**上抛（谁已生效、谁为何失败，逐条可指）。"""
    c = _ctr(tmp_path)
    for i in (1, 2, 3):
        _ingest(c, f"d{i}")
    _fail_on(c, "d2")

    with pytest.raises(ChangeApplyError) as got:
        c.ingest.apply_changes(
            [ChangeEvent(doc_id=f"d{i}", kind="delete", version=1, domain_before="fin") for i in (1, 2, 3)]
        )
    receipt = got.value.receipt
    assert receipt["applied"] == 2 and receipt["failed"] == 1 and receipt["blocked"] == 0
    assert receipt["deleted"] > 0, "已落地的清理必须留在账上（不吞效果）"
    rows = {(i["doc_id"]): i["outcome"] for i in receipt["items"]}
    assert rows == {"d1": "applied", "d2": "failed", "d3": "applied"}, "逐条隔离＝第 2 条失败不吞第 3 条"
    assert [(i["seq"], i["doc_id"]) for i in receipt["items"]] == [(1, "d1"), (2, "d2"), (3, "d3")], "序号＝提交序，不是执行分趟序"
    assert any("墓碑账写不进" in i["detail"] for i in receipt["items"] if i["doc_id"] == "d2")
    assert not c.ingest.is_indexed("d1") and not c.ingest.is_indexed("d3")


def test_failed_delete_item_names_what_already_landed(tmp_path) -> None:
    """单条 delete 的三步（摘两路→记墓碑→清清单）**不原子**：失败行的原因必须点名"已落地哪几步"＋出路。

    真实形态（本刀跑真实 HTTP 时才抓到的那条，非臆测）：`_purge` 成功、墓碑落账抛错 ⇒
    索引已摘而清单未清＝B5 背离债，且这条没有墓碑可走恢复。`failed` 一词若不含这些＝把"半条效果"说成"零效果"，
    与本档拦的"部分效果无账"同型；真做成原子需事务性双索引＝放行项②，本刀不假装给 dev 造一个。
    """
    c = _ctr(tmp_path)
    _ingest(c, "half")
    _fail_on(c, "half")

    with pytest.raises(ChangeApplyError) as got:
        c.ingest.apply_changes([ChangeEvent(doc_id="half", kind="delete", version=1)])
    receipt = got.value.receipt
    row = receipt["items"][0]
    assert row["outcome"] == "failed"
    # 已落地部分逐字点名（块数取自本批自己的账，不写死＝不靠"这块语料正好切几块"的巧合）
    assert f"已摘两路索引 {receipt['deleted']} 块、记墓碑 0 条" in row["detail"], row["detail"]
    assert "reconcile" in row["detail"] and "compensate" in row["detail"], "出路要写在账上，不靠运维记得"

    debt = c.ingest.reconcile(sense=False)
    assert debt["missing"] == receipt["deleted"] and debt["extra"] == 0, "债如实可见（清单在册、索引已摘）——不谎称这条没动"
    assert c.ingest.is_indexed("half"), "清单未清＝仍按指针在册，补偿有路"

    fix = c.ingest.compensate(write_report=False)  # 账上指的那扇门：按清单指针重放补齐
    assert fix["backfilled"] >= 1 and fix["missing_after"] == 0
    after = c.ingest.reconcile(sense=False)
    assert after["missing"] == 0 and after["extra"] == 0, "补齐后账实相符＝detail 指的路真能走通"


def test_every_change_in_the_batch_is_accounted_for(tmp_path) -> None:
    """不变式：applied＋blocked＋failed ＝＝ 批内条数（一条都不许从账面掉出去）。"""
    c = _ctr(tmp_path)
    _ingest(c, "keep")
    stats = c.ingest.apply_changes(
        [
            ChangeEvent(doc_id="keep", kind="delete", version=1),
            ChangeEvent(doc_id="ghost", kind="delete", version=1),  # 清单无登记＝blocked，不再静默算已处理
            ChangeEvent(doc_id="none", kind="upsert", version=1),  # 正文不在册＝blocked
            ChangeEvent(doc_id="keep", kind="acl_change", acl_tags_after=["hr"]),
        ]
    )
    assert stats["applied"] + stats["blocked"] + stats["failed"] == stats["total"] == 4
    assert stats["failed"] == 0
    assert "items" not in stats, "成功批不带逐条明细：int 回执面形状不变（明细只随异常带出）"
    assert all(isinstance(v, int) for v in stats.values())


def test_empty_batch_reports_zero_not_one(tmp_path) -> None:
    """空批：`total`＝真实条数 0（旧写法 `len(changes) or 1` 一个值两用，把"没有变更"报成"1 条变更、0 条删除"）。"""
    c = _ctr(tmp_path)
    _ingest(c, "keep")
    stats = c.ingest.apply_changes([])

    assert stats["total"] == 0 and stats["applied"] == 0 and stats["blocked"] == 0 and stats["failed"] == 0
    assert stats["applied"] + stats["blocked"] + stats["failed"] == stats["total"], "求和不变式对空批同样成立"
    assert c.ingest.is_indexed("keep"), "空批必须真的是零动作"


def test_domain_move_is_not_silently_dropped(tmp_path) -> None:
    """执行档没有 `domain_move` 的消费点：旧行为是从三个循环里凭空消失，现在如实落 blocked＋指路须 ADR。"""
    c = _ctr(tmp_path)
    _ingest(c, "mv")
    stats = c.ingest.apply_changes(
        [ChangeEvent(doc_id="mv", kind="domain_move", version=1, domain_before="fin", domain_after="hr")]
    )
    assert stats["blocked"] == 1 and stats["applied"] == 0 and stats["total"] == 1
    assert c.ingest.is_indexed("mv"), "零效果必须真的是零效果（不顺手改域）"


def test_blocked_reasons_are_readable_in_the_failure_ledger(tmp_path) -> None:
    """运维拿一条异常就能列处置清单：同一批里"被拒（两种原因）＋失败"全在 items 里点名。"""
    c = _ctr(tmp_path)
    _ingest(c, "ok")
    c.ingest._objects.put("blank/1", b"  ")
    _fail_on(c, "ok")
    with pytest.raises(ChangeApplyError) as got:
        c.ingest.apply_changes(
            [
                ChangeEvent(doc_id="none", kind="upsert", version=1, acl_tags_after=["fin"]),
                ChangeEvent(doc_id="blank", kind="upsert", version=1, acl_tags_after=["fin"]),
                ChangeEvent(doc_id="ok", kind="delete", version=1),
            ]
        )
    items = {i["doc_id"]: i for i in got.value.receipt["items"]}
    assert items["none"]["outcome"] == "blocked" and "正文不在册" in items["none"]["detail"]
    assert items["blank"]["outcome"] == "blocked" and "kind=delete" in items["blank"]["detail"]
    assert items["ok"]["outcome"] == "failed" and "落账故障" in items["ok"]["detail"]
    assert got.value.receipt["blocked"] == 1 + 1 and got.value.receipt["failed"] == 1


def test_http_change_receipt_exposes_the_three_counters(tmp_path) -> None:
    """门面 1:1 透传：HTTP 回执带上新三条计数（本层零判定，不自己再算一遍）。"""
    from fastapi.testclient import TestClient
    from src.platform.gateway.http import _create_app

    c = _ctr(tmp_path)
    _ingest(c, "page")
    client = TestClient(_create_app(c))
    r = client.post(
        "/api/ingest/change",
        json={"doc_id": "page", "kind": "delete", "confirm": True, "actor": "sre", "version": 1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] == 1 and body["blocked"] == 0 and body["failed"] == 0


def test_http_batch_failure_returns_the_ledger_not_a_bare_500(tmp_path) -> None:
    """逐条失败过门面：500 **带账**（旧行为＝栈外抛＝裸 500，运维只知道"坏了"不知道坏了哪条）。

    门面是两处冒出口（`/api/ingest/change` 与 `/api/ingest/dir` 经 `pull`）的共同翻译点，故用 exception_handler
    单点渲染，不在各路由里各写一遍 try/except。
    """
    from fastapi.testclient import TestClient
    from src.platform.gateway.http import _create_app

    c = _ctr(tmp_path)
    _ingest(c, "boom")
    _fail_on(c, "boom")
    client = TestClient(_create_app(c))
    r = client.post(
        "/api/ingest/change",
        json={"doc_id": "boom", "kind": "delete", "confirm": True, "actor": "sre", "version": 1},
    )
    assert r.status_code == 500
    body = r.json()
    assert "墓碑账写不进" in body["detail"]
    assert body["receipt"]["failed"] == 1 and body["receipt"]["applied"] == 0
    assert body["receipt"]["items"][0]["doc_id"] == "boom" and body["receipt"]["items"][0]["outcome"] == "failed"
    assert c.ingest.is_indexed("boom"), "门面把异常翻成 500 之后，失败那条仍不得被当成已完成"
