"""M3-c：FR-WIKI-2 脏页增量重编译 + FR-WIKI-3 源失效标记（经 canonical 流驱动，一域走查）。"""

from __future__ import annotations

from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.types import ChangeEvent, Document

_LONG = "# 政策\n\n" + "\n\n".join(f"第{i}段：执行档摄取与检索说明文字，长度足以沉淀 wiki。" for i in range(40))


def _publish(c, doc_id: str, domain: str = "hr") -> None:
    event = c.ingest.process_document(Document(doc_id=doc_id, version=1, domain=domain, doc_type="policy", acl_tags=[domain], raw_text=_LONG))
    assert c.wiki.run_once() >= 1
    page = c.wiki.drafts[-1]
    c.wiki.submit_for_review(page)
    assert c.wiki.publish(page, event, reviewer="owner") >= 1


def test_source_change_only_dirties_its_own_page() -> None:
    c = build_container(Settings())
    _publish(c, "d1")
    _publish(c, "d2")
    old1 = c.wiki.pages.get("wiki/hr/d1")
    assert old1 is not None and old1.status == "published"

    # d1 出新版本：只有 d1 的页标脏重编译，d2 的发布页不受牵连（不整库重跑）
    c.ingest.process_document(Document(doc_id="d1", version=2, domain="hr", doc_type="policy", acl_tags=["hr"], raw_text=_LONG + "\n\n补充新条款。"))
    c.wiki.run_once()

    assert old1.status == "stale"  # 旧发布页被标脏
    assert c.wiki.pages.get("wiki/hr/d1").status == "draft"  # 新草稿顶替，待人审
    assert c.wiki.pages.get("wiki/hr/d2").status == "published"  # 未被波及


def test_source_delete_marks_published_page_invalidated() -> None:
    c = build_container(Settings())
    _publish(c, "d1")
    page = c.wiki.pages.get("wiki/hr/d1")
    assert page is not None and page.status == "published"

    c.ingest.apply_changes([ChangeEvent(doc_id="d1", kind="delete", domain_before="hr")])
    c.wiki.run_once()

    assert page.status == "retracted"  # FR-WIKI-3：依据已失效
    assert any(e.kind == "wiki_invalidate" for e in c.audit.read_all())
