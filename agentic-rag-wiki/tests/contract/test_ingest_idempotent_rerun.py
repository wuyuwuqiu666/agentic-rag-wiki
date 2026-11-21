"""FR-ING-1 幂等收口（离线代码路径）：失败重跑不重复入库、中断恢复零脏数据。

幂等键=doc_id+version+chunk_seq——同一 Document 重放 process_document 后，向量索引、词法索引、
清单账三者计数均零翻倍，对账账实相符（无孤儿=无脏数据）。此前仅有 port 级
`test_vectorstore_idempotent_upsert_and_delete`，本用例补端到端管线口径：三路写（vectors/lexical/inventory）
逐字节复现同 chunk_id 且各自替换不追加。真实"断点续传/失败重跑"编排=PG 队列 + 双存储实机（放行项②），
此处在内存 stand-in 上验证幂等不变量本身。
"""

from __future__ import annotations

from pathlib import Path

from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.platform.providers.bm25_dev import Bm25Index
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Document

_BODY = "# 幂等标题\n\n" + "\n\n".join(
    f"段落 {i} 内容足够长以形成多个可检索块用于失败重跑不重复入库校验。" for i in range(6)
)


def _pipeline(tmp_path: Path):
    vs = MemoryVectorStore()
    bm = Bm25Index()
    inv = CanonicalInventory()
    pipeline = IngestPipeline(
        DevHashEmbedding(dim=8),
        vs,
        FileSystemObjectStore(str(tmp_path / "obj")),
        PgQueueMq(),
        inventory=inv,
        lexical=bm,
    )
    return pipeline, vs, bm, inv


def _doc() -> Document:
    return Document(doc_id="idem", version=1, domain="hr", acl_tags=["hr"], raw_text=_BODY)


def test_rerun_same_document_does_not_duplicate_anywhere(tmp_path: Path) -> None:
    pipeline, vs, bm, inv = _pipeline(tmp_path)

    pipeline.process_document(_doc())
    vec_after_1 = sorted(vs.all_ids())
    lex_after_1 = sorted(bm._by_id)
    total_len_1 = bm._total_len
    expected_1 = sorted(inv.expected_ids())
    assert vec_after_1  # 初灌多块，非空

    pipeline.process_document(_doc())  # 失败重跑（第 2 次）
    pipeline.process_document(_doc())  # 失败重跑（第 3 次）

    assert sorted(vs.all_ids()) == vec_after_1  # 向量索引：幂等键=chunk_id，重放不翻倍
    assert sorted(bm._by_id) == lex_after_1  # 词法索引：按 chunk_id 替换、不追加
    assert bm._total_len == total_len_1  # BM25 语料总 token 零虚增（重复入库会让其膨胀）
    assert sorted(inv.expected_ids()) == expected_1  # 清单账：按 (doc_id,version) 替换、不叠块


def test_rerun_keeps_ledger_reconciled_no_dirty_data(tmp_path: Path) -> None:
    """中断恢复零脏数据：重跑后对账既无缺失也无孤儿（账实逐一对齐）。"""
    pipeline, vs, _, inv = _pipeline(tmp_path)
    pipeline.process_document(_doc())
    pipeline.process_document(_doc())

    rec = pipeline.reconcile()
    assert rec["missing"] == 0  # 无幻影缺失
    assert rec["extra"] == 0  # 无孤儿=无重复脏块
    assert rec["expected"] == rec["actual"]
    assert set(vs.all_ids()) == inv.expected_ids()  # 索引实际态 = 清单应索引态，逐 id 相符
