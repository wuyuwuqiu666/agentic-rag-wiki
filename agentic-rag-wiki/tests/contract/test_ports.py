"""端口契约测试（双实现可换性 + fail-loud 不静默降级）。

同一套语义断言跑在 dev 实现上；云/本地档在依赖缺失时必须显式抛错（E2/NFR-8），绝不回退 dev。
"""

from __future__ import annotations

import pytest
from src.platform.providers.embedding_cloud import CloudEmbedding
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.embedding_local import LocalEmbedding
from src.platform.providers.errors import ProviderNotConfigured, VramBudgetExceeded
from src.platform.providers.mq_kafka import KafkaMq
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.ports import MqPort
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Chunk


def _chunk(cid: str, vec: list[float], acl: list[str]) -> Chunk:
    return Chunk(chunk_id=cid, doc_id="d", doc_version=1, seq=0, text=cid, acl_tags=acl, vector=vec)


def test_embedding_deterministic_and_normalized() -> None:
    emb = DevHashEmbedding(dim=64)
    a = emb.embed(["hello world", "another text"])
    b = emb.embed(["hello world", "another text"])
    assert len(a) == 2 and all(len(v) == 64 for v in a)
    assert a == b  # 确定性重放
    import math

    assert math.isclose(sum(x * x for x in a[0]), 1.0, rel_tol=1e-6)


def test_embedding_cloud_without_creds_fails_loud() -> None:
    with pytest.raises(ProviderNotConfigured):
        CloudEmbedding(base_url=None, api_key=None)


def test_embedding_local_batch_over_safety_fails_loud() -> None:
    emb = LocalEmbedding(batch_safe=2)  # 无需装 torch：越安全上界在加载前即拦
    with pytest.raises(VramBudgetExceeded):
        emb.embed(["a", "b", "c"])


def test_vectorstore_idempotent_upsert_and_delete() -> None:
    vs = MemoryVectorStore()
    vs.upsert([_chunk("c1", [1.0, 0.0], [])])
    vs.upsert([_chunk("c1", [1.0, 0.0], [])])  # 幂等键=chunk_id，重放不翻倍
    assert vs.count() == 1
    assert vs.delete(["c1", "missing"]) == 1
    assert vs.count() == 0


def test_vectorstore_acl_scoping() -> None:
    vs = MemoryVectorStore()
    vs.upsert([_chunk("pub", [1.0, 0.0], []), _chunk("fin", [1.0, 0.0], ["finance"])])
    hits = vs.search([1.0, 0.0], topk=10, acl_tags=["intern"])
    assert {c.chunk_id for c in hits} == {"pub"}  # 越权块不可见


def test_mqport_declares_depth_primitive() -> None:
    """E4 "摄取积压" 传感原语须为端口一等契约（换队列实现必须提供同语义 backlog，G5 可换性）。"""
    assert "depth" in dir(MqPort)  # 协议显式声明
    assert callable(PgQueueMq.depth)  # dev 实现满足


def test_mqport_declares_lease_ack_primitive() -> None:
    """ADR-011：`drain`=取得、`ack`=确认——确认缝必须是端口一等契约，否则换档实现会把"取走"当成"消费完"。

    只声明在 dev 档不算契约：KafkaMq 的 offset 提交与 PgQueueMq 的行锁释放都靠这条缝，
    缺它的实现能编译通过却静默丢事件（=本刀修的原始缺陷），故协议声明本身要锁。
    """
    assert "ack" in dir(MqPort)
    assert callable(PgQueueMq.ack) and callable(KafkaMq.ack)


def test_mq_backlog_tracks_publish_and_drain() -> None:
    """backlog 口径正确：publish 累加、drain **不**扣减（在租未确认仍是待办）、ack 才扣减、按 topic 隔离。

    "drain 即消费"的旧口径正是缺陷本体：中途失败时那批事件既不在页上也不在积压里＝无从发现。
    """
    mq = PgQueueMq()
    assert mq.depth("canonical") == 0
    for i in range(3):
        mq.publish("canonical", {"i": i})
    mq.publish("changes", {"i": 0})
    assert mq.depth("canonical") == 3  # 按 topic 隔离
    assert mq.drain("canonical", 2) == [{"i": 0}, {"i": 1}]
    assert mq.depth("canonical") == 3  # 取走≠消费完：在租 2 + 未取 1 仍计积压（卡住必须看得见）
    assert mq.ack("canonical", 1) == 1  # 确认前缀 1 条，其余退回队首
    assert mq.depth("canonical") == 2
    assert mq.drain("canonical", 5) == [{"i": 1}, {"i": 2}]  # 退回的保持相对次序＝重投不打乱事件先后
    assert mq.ack("canonical", 2) == 0
    assert mq.depth("canonical") == 0  # 走完租约才归零
    assert mq.depth("changes") == 1  # 未触他 topic


def test_mq_ack_out_of_range_fails_loud() -> None:
    """ack 越界＝消费侧计数错乱，必须抛错而非静默夹紧（夹紧会把"少确认"读成"都消费完了"）。"""
    mq = PgQueueMq()
    mq.publish("canonical", {"i": 0})
    mq.drain("canonical", 1)
    with pytest.raises(ValueError, match="在租 1 条，请求确认 2 条"):
        mq.ack("canonical", 2)
    with pytest.raises(ValueError, match="请求确认 -1 条"):
        mq.ack("canonical", -1)
    assert mq.depth("canonical") == 1  # 抛错不改账：既没消费也没退回
