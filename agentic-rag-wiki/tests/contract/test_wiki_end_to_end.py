"""M3-e FR-WIKI-5 检索优先 + 一域端到端走查（M3 出口门）。

一条链跑通：变更摄取 → triage 入选 → 草稿 → 人审门 → 发布回写 → 检索优先。
断言的是链路结构与"wiki 权威页优先于原文"的不变式，非模型质量基线（dev 合成档）。
"""

from __future__ import annotations

from src.partitions.ingest.service.ask import fusion
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.types import Chunk, Citation, Document

_TEXT = "# 差旅报销政策\n\n" + "\n\n".join(f"第{i}条：出差交通与住宿的报销标准与审批说明。" for i in range(40))


def test_prefer_wiki_orders_authority_first_stable() -> None:
    def c(cid: str, authority: str) -> Chunk:
        return Chunk(chunk_id=cid, doc_id=cid.split("#")[0], doc_version=1, seq=0, text="x", authority=authority)  # type: ignore[arg-type]

    ranked = [c("s1#0", "source"), c("w1#0", "wiki"), c("s2#0", "source"), c("w2#0", "wiki")]
    out = fusion.prefer_wiki(ranked)
    assert [x.chunk_id for x in out] == ["w1#0", "w2#0", "s1#0", "s2#0"]  # wiki 全部前置，组内保序


def test_one_domain_end_to_end_surfaces_wiki_authority_first() -> None:
    c = build_container(Settings())

    # 变更摄取：源文档入 A 区索引（authority=source），并 fan-out 给 B 区
    event = c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )

    # triage 入选（doc_type=policy 静态属性）→ 草稿
    assert c.wiki.run_once() >= 1
    page = c.wiki.drafts[-1]
    assert page.status == "draft"

    # 人审门：未过审不得直接发布（draft 态 + 空署名双重拦下，FR-WIKI-1 代码不变式）
    from src.partitions.wiki.service.review import ReviewError

    try:
        c.wiki.publish(page, event, reviewer="")
    except ReviewError:
        pass
    else:
        raise AssertionError("草稿未经人审应被发布门拦下")

    # 提交人审：draft → in_review（发布前置门）
    c.wiki.submit_for_review(page)

    # 即便已进 in_review，空署名仍不得发布
    try:
        c.wiki.publish(page, event, reviewer="")
    except ReviewError:
        pass
    else:
        raise AssertionError("空署名应被人审门拦下")

    # 署名通过后发布：生成回写块经 IngestionPort 入 A 区索引（authority=wiki）
    assert c.wiki.publish(page, event, reviewer="fin-owner") >= 1
    assert any(e.kind == "wiki_publish" for e in c.audit.read_all())

    # 检索优先：同域问答命中时，wiki 权威页排在原文之前
    ans = c.app.answer("出差住宿报销标准", user_acl=["fin"])
    assert not ans.refused
    assert ans.citations
    assert ans.citations[0].authority == "wiki"
    assert all(wiki_idx < src_idx for wiki_idx, src_idx in _authority_positions(ans.citations))


def _authority_positions(citations: list[Citation]) -> list[tuple[int, int]]:
    wiki = [i for i, ct in enumerate(citations) if ct.authority == "wiki"]
    src = [i for i, ct in enumerate(citations) if ct.authority != "wiki"]
    return [(w, s) for w in wiki for s in src]
