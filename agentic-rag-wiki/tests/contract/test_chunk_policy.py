"""FR-ING-7 切块策略：D-2 块型档位（text 聚合/句内切、table 原子+行组退化、code 函数级）、
F5 输入门限截断（非静默）、D-1 决议记录归档。

决议为 node 级（ADR-010）：判型/判级逐节点段记录，故一份文档可同时产出 text/table/code 三种块型。
本文件的规则档（第①级）判型由 `canonicalize` 的形状判据兑现；第②③级（版面小模型/LLM few-shot）
需真实模型或云出口＝实机放行项，此处不覆盖、不伪造。
"""

from __future__ import annotations

from pathlib import Path

from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.partitions.ingest.service import canonicalize, chunker
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import AuditEvent, Chunk, ChunkDecision, Document


def _chunk(seq: int, text: str, ct: str = "text", doc: str = "d", ver: int = 1) -> Chunk:
    return Chunk(
        chunk_id=f"{doc}/{ver}#{seq}",
        doc_id=doc,
        doc_version=ver,
        seq=seq,
        text=text,
        chunk_type=ct,  # type: ignore[arg-type]
        acl_tags=["hr"],
        token_count=len(text) // 4,
    )


# —— D-2 短节并入父节 ——
def test_merge_short_folds_tiny_into_previous_same_type() -> None:
    chunks = [_chunk(0, "字" * 2000), _chunk(1, "短尾"), _chunk(2, "字" * 2000)]
    merged = chunker.merge_short(chunks)
    assert len(merged) == 2  # <100 token 短节并入前一 text 块
    assert [c.seq for c in merged] == [0, 1]  # 重编号连续
    assert [c.chunk_id for c in merged] == ["d/1#0", "d/1#1"]
    assert merged[0].next == "d/1#1" and merged[1].prev == "d/1#0"  # 重链邻居
    assert "短尾" in merged[0].text  # 被并入前块


def test_merge_short_keeps_cross_type_atomic() -> None:
    # 短 table 块不得并入 text 父块（结构块保持原子，跨型不并）
    chunks = [_chunk(0, "字" * 2000), _chunk(1, "| a | b |", "table")]
    merged = chunker.merge_short(chunks)
    assert len(merged) == 2
    assert merged[1].chunk_type == "table"


def test_merge_short_is_deterministic() -> None:
    src = [_chunk(0, "字" * 2000), _chunk(1, "极短"), _chunk(2, "字" * 2000)]
    a = chunker.merge_short([c.model_copy(deep=True) for c in src])
    b = chunker.merge_short([c.model_copy(deep=True) for c in src])
    assert [(c.chunk_id, c.text) for c in a] == [(c.chunk_id, c.text) for c in b]


# —— F5 输入门限 ——
def test_f5_gate_truncates_over_limit_and_flags() -> None:
    big = _chunk(0, "字" * (9000 * 4))  # token 9000 > 8192 下游上界
    truncated = chunker.f5_gate([big])
    assert truncated == ["d/1#0"]
    assert big.token_count <= 8192
    assert big.extra.get("f5_truncated") is True


def test_f5_gate_leaves_normal_chunks_untouched() -> None:
    ok = _chunk(0, "字" * 800)
    assert chunker.f5_gate([ok]) == []
    assert not ok.extra.get("f5_truncated")


class _CollectAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


def _pipeline(tmp_path: Path, audit: _CollectAudit) -> tuple[IngestPipeline, MemoryVectorStore, FileSystemObjectStore]:
    vs = MemoryVectorStore()
    store = FileSystemObjectStore(str(tmp_path / "obj"))
    pipeline = IngestPipeline(
        DevHashEmbedding(dim=8),
        vs,
        store,
        PgQueueMq(),
        audit=audit,
        inventory=CanonicalInventory(),
    )
    return pipeline, vs, store


def test_pipeline_archives_decision_record(tmp_path: Path) -> None:
    """D-1：process_document 把 ChunkDecision 随 doc_version 归档 objectstore，可读回重放。"""
    pipeline, _, store = _pipeline(tmp_path, _CollectAudit())
    doc = Document(doc_id="doc1", version=2, domain="hr", acl_tags=["hr"], raw_text="段落一。\n\n段落二。")
    canonical, _ = canonicalize.canonicalize(doc, store)
    pipeline.process_document(doc)
    raw = store.get("doc1/2#decision")
    restored = ChunkDecision.model_validate_json(raw)  # 决议记录 round-trip
    planned = chunker.plan(canonical)
    assert restored.profile == planned.profile
    assert [(d.node_ids, d.chunk_type, d.decider) for d in restored.decisions] == [
        (d.node_ids, d.chunk_type, d.decider) for d in planned.decisions
    ]


def test_pipeline_f5_truncation_is_not_silent(tmp_path: Path) -> None:
    """病态超长单块→F5 截断必发 provider_fail_loud 审计（非静默丢尾），且入库块不越界。"""
    audit = _CollectAudit()
    pipeline, vs, _ = _pipeline(tmp_path, audit)
    pipeline.process_document(Document(doc_id="huge", version=1, domain="hr", acl_tags=["hr"], raw_text="字" * (9000 * 4)))
    assert any(e.kind == "provider_fail_loud" and "F5" in e.detail for e in audit.events)
    indexed = [vs._by_id[cid] for cid in vs.all_ids()]  # type: ignore[attr-defined]
    assert indexed and all(c.token_count <= 8192 for c in indexed)
