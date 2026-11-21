"""M4-cd④：`acl_change` 效应④＝wiki 页可见性重算 + 其回写块的 acl_tags 重合成（FR-ING-5 验收成对用例）。

PRD 明列"收紧权限后 wiki 页与回写块不得残留旧可见性"。此前源块的 ACL 从不随 `acl_change` 重写（效应②缺），
且 `acl_change` 不 fan-out（效应④缺）＝页与其权威块永久停在旧可见性上，旧域读者照样拿到 W 优先答案。

口径：源权限变更**不自动复审**——重算走的是同一条 canonical fan-out（`_acl_rewrite` 的重放随行），
B 区把已发布页标脏并当场撤其权威块；新的交集块要等下一次人审署名发布才回索引。
这不是偷懒，是 F2/FR-WIKI-1 的必然推论：**改了谁能看＝换了答案的受众，受众由人签字**。
"""

from __future__ import annotations

from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.types import ChangeEvent, Document

_TEXT = "# 差旅报销政策\n\n" + "\n\n".join(f"第{i}条：出差交通与住宿的报销标准与审批说明。" for i in range(40))
_QUESTION = "出差住宿报销标准"
_BLOCK = "wiki/fin/travel#0"


def _published(tmp_path):
    """真实链路发布一张 fin 域权威页：入库→编译→提交人审→署名发布。"""
    c = build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))  # type: ignore[arg-type]
    event = c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )
    assert c.wiki.run_once() >= 1
    page = c.wiki.drafts[-1]
    c.wiki.submit_for_review(page)
    assert c.wiki.publish(page, event, reviewer="fin-owner") >= 1
    return c, page


def _wiki_hit(c) -> bool:
    return any(ct.authority == "wiki" for ct in c.app.answer(_QUESTION, user_acl=["fin"]).citations)


def test_source_acl_narrowing_withdraws_the_authority_block_immediately(tmp_path) -> None:
    c, page = _published(tmp_path)
    assert _wiki_hit(c)

    c.ingest.apply_changes(
        [ChangeEvent(doc_id="travel", kind="acl_change", acl_tags_before=["fin"], acl_tags_after=["hr"])]
    )
    assert c.wiki.run_once() >= 1  # 重放随行的 fan-out 被 B 区消费＝效应④ 的执行位

    assert page.status == "stale"  # 依据的受众变了，未复审不得继续当权威
    assert _BLOCK not in set(c.vectorstore.all_ids())  # 旧可见性的回写块真的离了索引
    assert c.app.answer(_QUESTION, user_acl=["fin"]).refused  # 旧域读者：源块与权威块都读不到
    rec = c.ingest.reconcile()
    assert rec["writeback"] == 0 and rec["extra"] == 0 and rec["missing"] == 0  # 撤索引与撤账同批


def test_republished_block_carries_the_recomputed_intersection_acl(tmp_path) -> None:
    # 重合成的判据不是"又发了一次"，而是新块只对新域可见：旧域读者仍拿不到 W 优先答案。
    c, _ = _published(tmp_path)
    c.ingest.apply_changes(
        [ChangeEvent(doc_id="travel", kind="acl_change", acl_tags_before=["fin"], acl_tags_after=["hr"])]
    )
    c.wiki.run_once()

    fresh = c.wiki.drafts[-1]
    assert fresh.acl_tags == ["hr"]  # F2 交集重算：随源块新域收窄，不沿用页上的旧域
    c.wiki.submit_for_review(fresh)
    envelope = c.wiki.source_event(fresh.page_id)
    assert envelope is not None and envelope.acl_tags == ["hr"]
    assert c.wiki.publish(fresh, envelope, reviewer="hr-owner") >= 1

    assert any(ct.authority == "wiki" for ct in c.app.answer(_QUESTION, user_acl=["hr"]).citations)
    assert c.app.answer(_QUESTION, user_acl=["fin"]).refused  # 旧域不得因"重新发布"而复权
    assert c.ingest.reconcile()["writeback"] == 1  # 新块重新在册（不再是孤儿）


def test_acl_change_touches_no_other_pages_visibility(tmp_path) -> None:
    # 增量口径（FR-WIKI-2）：只重算受影响那张页，别的页不动、不撤、不标脏。
    c, _ = _published(tmp_path)
    c.ingest.process_document(
        Document(doc_id="expense", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )
    assert c.wiki.run_once() >= 1
    other = c.wiki.drafts[-1]
    c.wiki.submit_for_review(other)
    event = c.wiki.source_event(other.page_id)
    assert event is not None
    assert c.wiki.publish(other, event, reviewer="fin-owner") >= 1

    c.ingest.apply_changes(
        [ChangeEvent(doc_id="travel", kind="acl_change", acl_tags_before=["fin"], acl_tags_after=["hr"])]
    )
    c.wiki.run_once()
    assert other.status == "published"  # 未被波及的页继续当权威
    assert any(ct.authority == "wiki" for ct in c.app.answer(_QUESTION, user_acl=["fin"]).citations)
