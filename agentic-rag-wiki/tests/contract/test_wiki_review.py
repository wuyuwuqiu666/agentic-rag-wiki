"""M3-b：FR-WIKI-1 wiki 页生命周期状态机 + 人审发布门（纯函数、可单测）。

人审门=草稿不可直发、发布须署名——把"人审后才生效"当代码不变式执法（P2），非文档自觉。
"""

from __future__ import annotations

import pytest
from src.partitions.wiki.service import review
from src.partitions.wiki.service.review import ReviewError
from src.platform.types import WikiPage


def _page(status: str = "draft") -> WikiPage:
    return WikiPage(page_id="wiki/hr/d1", domain="hr", title="d1", status=status)  # type: ignore[arg-type]


def test_happy_path_draft_to_published_records_reviewer() -> None:
    page = _page()
    review.submit_for_review(page)
    assert page.status == "in_review"
    review.approve(page, "alice")
    assert page.status == "published"
    assert page.reviewer == "alice"


def test_draft_cannot_publish_directly() -> None:
    with pytest.raises(ReviewError):
        review.approve(_page("draft"), "alice")  # 越门：draft 未过审


def test_publish_requires_signature() -> None:
    page = _page("in_review")
    with pytest.raises(ReviewError):
        review.approve(page, "   ")  # 未署名不得发布
    assert page.status == "in_review"  # 抛错不改动状态


def test_reject_returns_to_draft() -> None:
    page = _page("in_review")
    review.reject(page)
    assert page.status == "draft"


@pytest.mark.parametrize("status", ["draft", "published"])
def test_illegal_transitions_rejected(status: str) -> None:
    with pytest.raises(ReviewError):
        review.reject(_page(status))  # 仅 in_review 可打回


def test_stale_and_retract_lifecycle() -> None:
    pub = _page("published")
    review.mark_stale(pub)
    assert pub.status == "stale"
    review.submit_for_review(pub)  # 失效→重编译后再审
    review.approve(pub, "bob")
    assert pub.status == "published"

    retracted = _page("published")
    review.retract(retracted)  # 源删除→依据已失效（FR-WIKI-3）
    assert retracted.status == "retracted"


def test_check_approve_precheck_is_side_effect_free() -> None:
    """发布门预检（供发布链路"先判门再写"用）：合法即静默返回，但绝不改本页状态或署名。"""
    page = _page("in_review")
    review.check_approve(page, "alice")
    assert page.status == "in_review" and page.reviewer == ""
    with pytest.raises(ReviewError):
        review.check_approve(_page("draft"), "alice")


class _RefusingIngestion:
    """A 区入口拒绝 stand-in：B0 拉闸、F2 空 ACL、NFR-8 越界都在 `IngestionPort.admit` 抛。

    `is_indexed` 返回 True＝本例只验"写侧被拒不改状态"，依据存活门须放行到此（源视为在册）。
    """

    def admit(self, event: object, chunks: list[object]) -> int:
        raise RuntimeError("写侧已冻结")

    def is_indexed(self, doc_id: str) -> bool:
        return True


def test_publish_does_not_commit_state_when_write_side_refuses(tmp_path) -> None:
    """写侧被拒不等于审阅通过：回写没落地就不把页改成 published（否则=页声称权威、索引里却没有的账实背离）。"""
    from src.partitions.wiki.runtime.loop import WikiLoop
    from src.platform.providers.mq_pgqueue import PgQueueMq
    from src.platform.providers.objectstore_fs import FileSystemObjectStore
    from src.platform.types import CanonicalEvent

    loop = WikiLoop(PgQueueMq(), _RefusingIngestion(), FileSystemObjectStore(str(tmp_path / "obj")))
    page = _page("in_review")
    with pytest.raises(RuntimeError):
        loop.publish(page, CanonicalEvent(doc_id="d1", version=1, domain="hr", acl_tags=["hr"]), reviewer="alice")
    assert page.status == "in_review" and page.reviewer == ""
