"""FR-ING-2 delete 精确化（离线收口）：按清单解析真实 chunk_id 删除 + 删后同步清账保持账实相符。

替换原 `delete([f"{doc_id}"])` doc 前缀占位（在精确 pop 的向量库里删不掉任何块）。
"""

from __future__ import annotations

from pathlib import Path

from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import ChangeEvent, Document

_BODY = "# 标题\n\n" + "\n\n".join(f"段落 {i} 内容足够长以形成多个可检索块用于删除精确化校验。" for i in range(6))


def _pipeline(tmp_path: Path) -> tuple[IngestPipeline, MemoryVectorStore, CanonicalInventory]:
    vs = MemoryVectorStore()
    inv = CanonicalInventory()
    pipeline = IngestPipeline(
        DevHashEmbedding(dim=8),
        vs,
        FileSystemObjectStore(str(tmp_path / "obj")),
        PgQueueMq(),
        inventory=inv,
    )
    return pipeline, vs, inv


def test_delete_removes_exact_chunks_and_keeps_ledger(tmp_path: Path) -> None:
    pipeline, vs, inv = _pipeline(tmp_path)
    pipeline.process_document(Document(doc_id="del", version=1, domain="hr", acl_tags=["hr"], raw_text=_BODY))
    doc_ids = [cid for cid in vs.all_ids() if cid.startswith("del/")]
    assert doc_ids  # 初灌多块

    outcome = pipeline.apply_changes([ChangeEvent(doc_id="del", kind="delete")])

    assert outcome["deleted"] == len(doc_ids)  # 真实 chunk 全删（非占位删 0）
    assert not [cid for cid in vs.all_ids() if cid.startswith("del/")]  # 索引无残留
    assert "del/1#0" not in inv.expected_ids()  # 清单同步 forget
    assert pipeline.reconcile()["missing"] == 0  # 删后账实相符（不再是幻影缺失）


def test_delete_specific_version_leaves_others(tmp_path: Path) -> None:
    pipeline, vs, _ = _pipeline(tmp_path)
    pipeline.process_document(Document(doc_id="vv", version=1, acl_tags=["hr"], raw_text=_BODY))
    pipeline.process_document(Document(doc_id="vv", version=2, acl_tags=["hr"], raw_text=_BODY))
    v1 = [cid for cid in vs.all_ids() if cid.startswith("vv/1#")]
    v2 = [cid for cid in vs.all_ids() if cid.startswith("vv/2#")]
    assert v1 and v2

    pipeline.apply_changes([ChangeEvent(doc_id="vv", version=2, kind="delete")])

    assert not [cid for cid in vs.all_ids() if cid.startswith("vv/2#")]  # 仅删 v2
    assert set(v1) <= set(vs.all_ids())  # v1 原样保留
    assert pipeline.reconcile()["missing"] == 0  # v2 清账、v1 仍在账，账实相符
