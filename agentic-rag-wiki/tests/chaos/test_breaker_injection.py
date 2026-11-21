"""混沌/熔断注入用例（P4）：批量删除触发 B1 硬断；ACL 收窄后越权即刻不可见（F3 成对）。"""

from __future__ import annotations

from src.partitions.ingest.service.ask import cache
from src.partitions.wiki.service.compile import _intersect_acl
from src.platform.breaker import evaluate
from src.platform.breaker.rules import Rule
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.types import Answer, BreakerLevel, ChangeEvent, Chunk, Citation

_BODY = "# t\n\n" + "\n\n".join(f"段 {i} 内容文字足够长以形成多块。" for i in range(30))


def _chunks(acls: list[list[str]]) -> list[Chunk]:
    return [Chunk(chunk_id=f"c{i}", doc_id="d", doc_version=1, seq=i, text="x", acl_tags=a) for i, a in enumerate(acls)]


def test_batch_delete_ratio_hard_trips_b1() -> None:
    b1 = Rule(breaker="B1", metric="batch_delete_ratio", comparator=">=", soft=0.01, hard=0.10, level=BreakerLevel.HARD)
    total, deleted = 100, 15
    d = evaluate("batch_delete_ratio", deleted / total, b1)
    assert d.tripped and d.action == "block"  # 15% 删除→硬断，流水线停在此处


def test_acl_intersection_narrows_visibility() -> None:
    # 源块 ACL 交集为空→回写页收窄为 restricted（不放行全量）
    narrowed = _intersect_acl(_chunks([["hr"], ["finance"]]), fallback=[])
    assert narrowed == ["restricted"]
    shared = _intersect_acl(_chunks([["hr", "read"], ["hr", "write"]]), fallback=[])
    assert shared == ["hr"]


def test_acl_change_reindex_hides_from_outsider() -> None:
    container = build_container(Settings())
    vs = container.vectorstore
    vec = container.embedding.embed(["政策内容"])[0]

    def acl_chunk(acl: list[str]) -> Chunk:
        return Chunk(chunk_id="doc#0", doc_id="doc", doc_version=1, seq=0, text="政策内容", acl_tags=acl, vector=vec)

    vs.upsert([acl_chunk(["hr"])])  # 初灌：hr 可见
    assert [c for c in vs.search(vec, topk=5, acl_tags=["hr"])]
    vs.upsert([acl_chunk(["finance"])])  # acl_change 重索引：同键收窄到 finance
    assert not [c for c in vs.search(vec, topk=5, acl_tags=["hr"])]  # hr 用户即刻不可见（F3）


def _ans(question: str) -> Answer:
    return Answer(question=question, text="有据答案", citations=[Citation(chunk_id="d#0", doc_id="d", doc_version=1)])


def test_acl_change_evicts_semantic_cache_by_domain() -> None:
    """FR-ING-5③ 成对：acl_change 落地后，受影响 ACL 域的语义缓存即刻剔除（旧权限答案不得再命中）。

    效应①②（PG 权限行 / Milvus 块标量）需双存储实机=放行项，本用例只锁离线可验的缓存收口 + 域定向。
    """
    container = build_container(Settings())
    container.cache.put(cache.cache_key("薪酬制度", ["hr"]), _ans("薪酬制度"))
    container.cache.put(cache.cache_key("合同模板", ["legal"]), _ans("合同模板"))
    assert container.cache.get(cache.cache_key("薪酬制度", ["hr"])) is not None

    outcome = container.ingest.apply_changes(
        [ChangeEvent(doc_id="d", kind="acl_change", acl_tags_before=["hr"], acl_tags_after=["finance"])]
    )

    assert outcome["cache_evicted"] == 1  # 仅命中受影响域（before∪after）
    assert container.cache.get(cache.cache_key("薪酬制度", ["hr"])) is None  # hr 域已清
    assert container.cache.get(cache.cache_key("合同模板", ["legal"])) is not None  # 无关域不误清

