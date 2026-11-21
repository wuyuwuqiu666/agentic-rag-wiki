"""M0 冒烟闭环：摄取→fan-out→wiki 草稿→回写→检索优先→答案，并断言一次熔断触发。"""

from __future__ import annotations

import pytest
from src.partitions.wiki.service.review import ReviewError
from src.platform.breaker import evaluate
from src.platform.breaker.rules import Rule
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.types import BreakerLevel, Document

_LONG = "# 政策\n\n" + "\n\n".join(f"第{i}段：企业知识库存量摄取与检索的执行档说明文字内容。" for i in range(30))


def test_end_to_end_smoke() -> None:
    container = build_container(Settings())
    doc = Document(doc_id="d1", version=1, title="存量政策", domain="hr", doc_type="policy", acl_tags=["hr"], raw_text=_LONG)

    event = container.ingest.process_document(doc)
    assert event.store_key and event.checksum
    assert container.vectorstore.count() >= 1  # type: ignore[attr-defined]

    made = container.wiki.run_once()
    assert made >= 1
    draft = container.wiki.drafts[0]
    assert draft.acl_tags and draft.triage_reason

    # 人审门：草稿未过审不得直发（FR-WIKI-1）
    with pytest.raises(ReviewError):
        container.wiki.publish(draft, event, reviewer="hr_admin")
    container.wiki.submit_for_review(draft)

    # 发布回写：wiki 块以高权威入 A 区索引
    assert container.wiki.publish(draft, event, reviewer="hr_admin") >= 1

    answer = container.app.answer("存量摄取怎么做", user_acl=["hr"])
    assert not answer.refused
    assert answer.citations  # FR-ASK-2 引用强制
    assert all(c.chunk_id for c in answer.citations)


def test_acl_filter_blocks_outsider() -> None:
    container = build_container(Settings())
    container.ingest.process_document(
        Document(doc_id="secret", domain="finance", acl_tags=["finance"], raw_text=_LONG)
    )
    outsider = container.app.answer("工资数据", user_acl=["intern"])
    assert outsider.refused and not outsider.citations  # 越权域检索为空即拒


def test_b1_hard_breaker_trips() -> None:
    b1 = Rule(breaker="B1", metric="batch_delete_ratio", comparator=">=", soft=0.01, hard=0.10, level=BreakerLevel.HARD)
    assert evaluate("batch_delete_ratio", 0.20, b1).action == "block"  # 超硬阈→阻断流水线
    assert evaluate("batch_delete_ratio", 0.001, b1).action == "none"
