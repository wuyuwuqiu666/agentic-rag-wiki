"""FR-WIKI-5 F2 回写 ACL IngestionPort 入口硬校验（离线纵深）：封"跨源页面泄露"通道。

B 区 compile 已把回写块 ACL 交合成源交集（空标 restricted）；本测锁 A 区入口纵深——回写块绝不按
event 回填/放大 ACL，空 ACL fail-loud；source 块回填原行为保留。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.errors import ProviderError
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import CanonicalEvent, Chunk


def _pipeline(tmp_path: Path) -> tuple[IngestPipeline, MemoryVectorStore]:
    vs = MemoryVectorStore()
    pipeline = IngestPipeline(
        DevHashEmbedding(dim=8),
        vs,
        FileSystemObjectStore(str(tmp_path / "obj")),
        PgQueueMq(),
        inventory=CanonicalInventory(),
    )
    return pipeline, vs


def _event(acl: list[str]) -> CanonicalEvent:
    return CanonicalEvent(doc_id="src", version=1, domain="policy", acl_tags=acl, store_key="src/1")


def _chunk(acl: list[str], authority: str) -> Chunk:
    return Chunk(
        chunk_id=f"wb/{authority}#0",
        doc_id=f"wb/{authority}",
        doc_version=1,
        seq=0,
        text="回写正文",
        acl_tags=acl,
        authority=authority,  # type: ignore[arg-type]
        token_count=2,
    )


def test_wiki_writeback_empty_acl_is_rejected(tmp_path: Path) -> None:
    pipeline, vs = _pipeline(tmp_path)
    with pytest.raises(ProviderError, match="跨源页面泄露"):
        pipeline.admit(_event(["hr"]), [_chunk([], "wiki")])  # 空 ACL 回写=欲放行公开，必拒
    assert not vs.all_ids()  # 未入索引


def test_wiki_writeback_acl_never_widened_by_event(tmp_path: Path) -> None:
    pipeline, vs = _pipeline(tmp_path)
    # 页面交集=restricted（窄），event=源单档 hr（宽）：入口绝不用 event 覆盖/放大回写块 ACL
    pipeline.admit(_event(["hr"]), [_chunk(["restricted"], "wiki")])
    stored = next(vs._by_id[cid] for cid in vs.all_ids())  # type: ignore[attr-defined]
    assert stored.acl_tags == ["restricted"]


def test_source_chunk_empty_acl_still_backfilled(tmp_path: Path) -> None:
    pipeline, vs = _pipeline(tmp_path)
    pipeline.admit(_event(["hr"]), [_chunk([], "source")])  # 原行为：source 块缺 ACL 按 event 回填
    stored = next(vs._by_id[cid] for cid in vs.all_ids())  # type: ignore[attr-defined]
    assert stored.acl_tags == ["hr"]
