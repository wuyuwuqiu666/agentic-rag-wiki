"""F1 契约验收：事件重放与清单重拉逐字节一致（canonical 正文走 ObjectStore，信封只带指针）。"""

from __future__ import annotations

import hashlib

from src.partitions.ingest.service import canonicalize, chunker
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.types import Document

_BODY = "# 标题\n\n" + "\n\n".join(f"第{i}段 说明性正文，长度足以形成多个检索块用于确定性校验。" for i in range(20))


def test_replay_equals_pull(tmp_path) -> None:
    store = FileSystemObjectStore(str(tmp_path / "draft"))
    doc = Document(doc_id="d1", version=1, acl_tags=["hr"], raw_text=_BODY)

    canonical, event = canonicalize.canonicalize(doc, store)
    body = store.get(event.store_key)  # 按信封指针从清单重拉正文
    recomputed = hashlib.sha256(body).hexdigest()
    assert event.checksum == canonical.checksum == recomputed  # 逐字节一致

    # 切块=对决议的确定性重放：同正文两次切块结果完全相同
    chunks_a = chunker.apply(canonical, chunker.plan(canonical))
    chunks_b = chunker.apply(canonical, chunker.plan(canonical))
    assert [(c.chunk_id, c.text, c.seq) for c in chunks_a] == [(c.chunk_id, c.text, c.seq) for c in chunks_b]
    assert all(c.chunk_id and c.text for c in chunks_a)


def test_envelope_carries_no_body(tmp_path) -> None:
    store = FileSystemObjectStore(str(tmp_path / "draft"))
    _, event = canonicalize.canonicalize(Document(doc_id="d2", version=3, raw_text=_BODY), store)
    dumped = event.model_dump()
    assert "raw_text" not in dumped and dumped["store_key"] == "d2/3"  # F1：只指针，不内联正文
