"""M4-cc/M4-cd 回写块与删除的"服务生命周期"：标了失效就必须真的停止供答案，删了就必须两路都摘净。

锁五条不变式（此前均为"改了状态、没改服务"）：
① 删除＝向量路＋词法路＋语义缓存同清（只摘向量路时 BM25 仍把已删块召回并写进引用）；
② 页离开 published（源删除→retracted／源变更→stale）＝其权威块经 IngestionPort 撤回（不再以 W 优先命中）；
③ 撤回仍是写侧动作，同过 B0 总闸，不开后门；
④ 依据已失效＝**未发布的草稿也升不了权威**（M4-cd：`_invalidate` 只够得着 published 页，
   草稿/待审页靠发布前的"依据存活门"拦，判据取自 A 区清单而非 B 区第二本账）；
⑤ acl_change 的受众收紧分两段：源块即刻、权威块到下一轮编译（窗口如实可测，不写成"即刻改完受众"）。
"""

from __future__ import annotations

import pytest
from src.partitions.wiki.service.review import ReviewError
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.gateway.composition import Container
from src.platform.providers.errors import BreakerTripped
from src.platform.types import ChangeEvent, Document

_TEXT = "# 差旅报销政策\n\n" + "\n\n".join(f"第{i}条：出差交通与住宿的报销标准与审批说明。" for i in range(40))
_QUESTION = "出差住宿报销标准"


def _published(tmp_path, *, hard_breakers: bool = False) -> tuple[Container, object, object]:
    """真实链路产出一张已发布权威页：入库→编译→提交人审→署名发布。

    默认留 B1 shadow（例行单条删除只记不拦，同执行档口径）；`hard_breakers` 供"总闸已拉闸"那一例显式转硬断。
    """
    kwargs: dict[str, object] = {"artifact_dir": str(tmp_path), "audit_dir": str(tmp_path / "audit")}
    if hard_breakers:
        kwargs["shadow_breakers"] = ""  # 显式转硬断：该例要验的就是"拉闸真的冻住写侧"
    c = build_container(Settings(**kwargs))  # type: ignore[arg-type]
    event = c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )
    assert c.wiki.run_once() >= 1
    page = c.wiki.drafts[-1]
    c.wiki.submit_for_review(page)
    assert c.wiki.publish(page, event, reviewer="fin-owner") >= 1
    return c, event, page


def _hit_wiki(c: Container) -> bool:
    ans = c.app.answer(_QUESTION, user_acl=["fin"])
    return any(ct.authority == "wiki" for ct in ans.citations)


def _draft(tmp_path) -> tuple[Container, object]:
    """真实链路产出一张**尚未发布**的草稿页（入库→编译），供"依据已失效的草稿能否升权威"两例用。"""
    c = build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))  # type: ignore[arg-type]
    c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )
    assert c.wiki.run_once() == 1
    return c, c.wiki.drafts[-1]


def test_deleted_source_blocks_its_unpublished_draft_from_publishing(tmp_path) -> None:
    """M4-cd④（M4-ce 1a 在册的那个洞）：源删除后，草稿/待审页不得被署名"复活"成权威块。

    `_invalidate` 只撤 `status=="published"` 的页，`delete→重放→再 delete` 之后页停在 `draft`；
    缺这道门时审核台一点"通过"就把依据已失效的内容当 W 优先供答案（FR-WIKI-3 的反面）。
    """
    c, page = _draft(tmp_path)
    c.ingest.apply_changes([ChangeEvent(doc_id="travel", kind="delete", domain_before="fin")])
    assert c.wiki.run_once() == 0  # tombstone 轮：不产草稿，也不改这张草稿的状态

    c.wiki.submit_for_review(page)  # 人审流程照走——门拦在发布那一刻，不拦审阅
    envelope = c.wiki.source_event(page.page_id)
    assert envelope is not None
    with pytest.raises(ReviewError, match="依据已失效"):
        c.wiki.publish(page, envelope, reviewer="fin-owner")
    assert page.status == "in_review"  # 拒绝不改状态（同"写侧被拒不等于审阅通过"口径）
    assert "wiki/fin/travel#0" not in set(c.vectorstore.all_ids())  # 一个块也没回索引
    assert c.app.answer(_QUESTION, user_acl=["fin"]).refused


def test_restore_of_the_source_makes_the_same_draft_publishable_again(tmp_path) -> None:
    # 门拦的是"依据不在了"，不是"这张页有罪"：源按 §10 回滚重放回清单后，同一张草稿即可正常发布。
    c, page = _draft(tmp_path)
    c.ingest.apply_changes([ChangeEvent(doc_id="travel", kind="delete", domain_before="fin")])
    c.wiki.run_once()
    c.wiki.submit_for_review(page)
    envelope = c.wiki.source_event(page.page_id)
    assert envelope is not None
    assert not c.ingest.is_indexed("travel")

    assert c.ingest.restore_deletes(doc_id="travel")["restored"] == 1
    assert c.ingest.is_indexed("travel")  # 在册判据随之翻转（A 区清单＝唯一真相，无需 B 区补记）
    assert c.wiki.publish(page, envelope, reviewer="fin-owner") >= 1
    assert _hit_wiki(c)


def test_source_delete_clears_both_recall_paths_and_stops_answering(tmp_path) -> None:
    # 先答一次（populate ①层缓存），删源后同问同 ACL 不得再从任何一路供出答案。
    c, _, page = _published(tmp_path)
    assert _hit_wiki(c)
    c.ingest.apply_changes([ChangeEvent(doc_id="travel", kind="delete", domain_before="fin")])
    assert c.wiki.run_once() == 0  # 本轮是 tombstone，不产草稿

    ans = c.app.answer(_QUESTION, user_acl=["fin"])
    assert ans.refused and ans.citations == []  # 词法路/缓存有残留时这里就会给出已删块
    assert page.status == "retracted"


def test_withdrawn_block_leaves_no_orphan_in_reconcile(tmp_path) -> None:
    # 权威块撤干净后，B5 对账既不缺块也不虚报孤儿（残留块会以"索引有、清单无"的孤儿形态污染对账报告）。
    c, _, _ = _published(tmp_path)
    c.ingest.apply_changes([ChangeEvent(doc_id="travel", kind="delete", domain_before="fin")])
    c.wiki.run_once()
    stats = c.ingest.reconcile()
    assert stats["missing"] == 0 and stats["extra"] == 0 and stats["actual"] == 0


def test_published_writeback_block_is_in_the_expected_ledger_not_an_orphan(tmp_path) -> None:
    """M4-cd①：发布态的权威块是"应当已索引"的一部分，不得再以孤儿形态虚报。

    缺此册时每发布一页就固定多出 N 个"索引有、清单无"的孤儿，真背离（索引掉块）被这坨恒定噪声淹没；
    补账后 `extra` 只报真孤儿，且 `writeback` 让运维看得出账里有几块属权威页（处置路径与源块不同）。
    """
    c, _, _ = _published(tmp_path)
    stats = c.ingest.reconcile()
    assert stats["writeback"] >= 1  # 发布过的块在册
    assert stats["extra"] == 0  # 不再是孤儿
    assert stats["missing"] == 0
    assert "wiki/fin/travel#0" in c.inventory.writeback_ids()
    assert c.inventory.writeback_pages() == {"wiki/fin/travel": ["wiki/fin/travel#0"]}


def test_lost_authority_block_is_b5_debt_and_is_never_fabricated_by_compensate(tmp_path) -> None:
    """M4-cd① 的另一半：权威块从索引掉了＝真背离要报出来，但 A 区补偿**不得**凭 canonical 正文造页内容。

    源块缺了可按清单指针重拉 objectstore 正文重放；权威块正文只活在 B 区的页，重放只会造出源块。
    故本作业只如实记账（backfilled 不含它、missing 仍为 1），处置指回 B 区重编译→人审→重发布。
    """
    c, _, _ = _published(tmp_path)
    c.vectorstore.delete(["wiki/fin/travel#0"])  # 模拟索引侧掉块（未经 withdraw，故账上仍"应索引"）
    rec = c.ingest.reconcile()
    assert rec["missing"] == 1 and rec["extra"] == 0
    summary = c.ingest.compensate(now=1700000000.0, write_report=False)
    assert summary["backfilled"] == 0  # 不臆造那张页
    assert summary["missing"] == 1  # 背离债留在账上，交 B 区处置
    assert summary["writeback"] == 1


def test_stale_page_loses_wiki_authority_until_republished(tmp_path) -> None:
    # 源出新版本＝依据待复审：页标脏、权威块撤下（原文层照常供答案），重发布后 W 优先恢复。
    c, _, page = _published(tmp_path)
    assert _hit_wiki(c)

    c.ingest.process_document(
        Document(
            doc_id="travel",
            version=2,
            domain="fin",
            doc_type="policy",
            acl_tags=["fin"],
            raw_text=_TEXT + "\n\n第 41 条：新增审批层级。",
        )
    )
    assert c.wiki.run_once() >= 1
    assert page.status == "stale"
    assert all(ct.authority != "wiki" for ct in c.app.answer(_QUESTION, user_acl=["fin"]).citations)

    fresh = c.wiki.drafts[-1]
    c.wiki.submit_for_review(fresh)
    envelope = c.wiki.source_event(fresh.page_id)
    assert envelope is not None and envelope.version == 2  # 发布用的是本轮源信封，不凭页内容伪造（F1）
    assert c.wiki.publish(fresh, envelope, reviewer="fin-owner") >= 1
    assert _hit_wiki(c)


def test_acl_change_narrows_source_now_and_the_published_block_at_compile(tmp_path) -> None:
    """M4-cd④ 的窗口口径（钉死，不许悄悄改行为）：改域即刻收紧**源块**，已发布权威块仍按页旧域可读，直到下一轮编译撤块。

    这条只能由"旧域还能不能命中 W 块"来判，所以写进测试而不只写进回执文案：
    若哪天把④做成即刻重算（不再需要人重新署名），此处的断言会先响——那时要一起改的是回执与 PRD 口径。
    """
    c, _, page = _published(tmp_path)
    c.ingest.apply_changes(
        [ChangeEvent(doc_id="travel", kind="acl_change", acl_tags_before=["fin"], acl_tags_after=["hr"])]
    )
    # 源路即刻换域：fin 读者拿不到 source 引用（两路皆已重写），hr 读者照常拿得到
    fin_first = c.app.answer(_QUESTION, user_acl=["fin"])
    assert all(ct.authority != "source" for ct in fin_first.citations)
    assert any(ct.authority == "wiki" for ct in fin_first.citations)  # 旧权威块仍在（页尚未过编译窗）
    hr = c.app.answer(_QUESTION, user_acl=["hr"])
    assert any(ct.authority == "source" for ct in hr.citations) and all(ct.authority != "wiki" for ct in hr.citations)

    c.wiki.run_once()  # ④：受影响页在下一轮编译标脏撤块，窗口就此关闭
    assert page.status == "stale"
    assert c.app.answer(_QUESTION, user_acl=["fin"]).refused

    fresh = c.wiki.drafts[-1]
    c.wiki.submit_for_review(fresh)
    assert fresh.acl_tags == ["hr"]  # 交集随源重算＝新受众
    assert c.wiki.publish(fresh, c.wiki.source_event(fresh.page_id), reviewer="hr-owner") >= 1
    assert any(ct.authority == "wiki" for ct in c.app.answer(_QUESTION, user_acl=["hr"]).citations)
    assert all(ct.authority != "wiki" for ct in c.app.answer(_QUESTION, user_acl=["fin"]).citations)


def test_withdraw_does_not_bypass_the_master_breaker(tmp_path) -> None:
    # B0"冻结全部写侧"含撤回：拉闸期撤不下＝如实 fail-loud，不给"失效块静默继续服务"留旁路。
    c, _, _ = _published(tmp_path, hard_breakers=True)
    for _ in range(3):
        with pytest.raises(BreakerTripped):
            c.guard.check("batch_delete_ratio", 0.5, "B1", partition="ingest")

    with pytest.raises(BreakerTripped):
        c.ingest.withdraw(["wiki/fin/travel#0"], ["fin"])
    assert _hit_wiki(c)  # 冻结期读侧照旧：块仍在索引，复位后由重编译补撤（同 §6 承诺，不是入口被绕过）


def test_block_withdrawal_is_auditable(tmp_path) -> None:
    # 撤块须留痕（与 wiki_publish 对偶）：审计里能回答"这张页什么时候因为什么不再是权威"。
    c, _, page = _published(tmp_path)
    c.ingest.apply_changes([ChangeEvent(doc_id="travel", kind="delete", domain_before="fin")])
    c.wiki.run_once()
    events = [e for e in c.audit.read_all() if e.kind == "wiki_invalidate"]
    assert any(page.page_id in e.doc_id and "撤下权威块" in e.detail for e in events)
