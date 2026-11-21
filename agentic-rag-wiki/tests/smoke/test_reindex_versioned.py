"""FR-ING-3 版本化索引重建：影子→原子切换→失败自动回滚旧别名→回滚也失败 fail-loud（离线控制不变式）。

数值验收（切换期查询错误率 <0.1%）属真实 PG+Milvus 双 collection 容量演练（放行项）；此处锁机制：
build 先于 flip、flip 失败必回滚旧别名、回滚失败必抛 ProviderError（别名不得悬空/静默）、别名同名即拒。
末条走真实 IngestPipeline + MemoryVectorStore，证明该路径可达而非孤立。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.partitions.ingest.service import reindex
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.errors import ProviderError
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Document


class _FakeStore:
    """记录 alias_swap 调用序列；可按序号注入故障（0=切换，1=回滚）；可选共享 log 记录交织顺序。"""

    def __init__(self, *, fail_flip: bool = False, fail_rollback: bool = False, log: list[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.current: str | None = None
        self._fail_flip = fail_flip
        self._fail_rollback = fail_rollback
        self._log = log

    def alias_swap(self, old: str, new: str) -> None:
        idx = len(self.calls)
        self.calls.append((old, new))
        if self._log is not None:
            self._log.append("flip")
        if idx == 0 and self._fail_flip:
            raise RuntimeError("flip boom")
        if idx == 1 and self._fail_rollback:
            raise RuntimeError("rollback boom")
        self.current = new


def _run(store: _FakeStore, events: list[str]) -> reindex.ReindexOutcome:
    return reindex.run_shadow_reindex(  # type: ignore[arg-type]
        store,
        active_alias="active",
        shadow_alias="shadow",
        build_shadow=lambda: events.append("build"),
    )


def test_success_flips_to_shadow_after_build() -> None:
    events: list[str] = []
    store = _FakeStore()
    out = _run(store, events)
    assert out.applied is True and out.rolled_back is False
    assert out.active_alias == "shadow" and out.error is None
    assert events == ["build"] and store.calls == [("active", "shadow")]
    assert store.current == "shadow"


def test_build_happens_before_flip() -> None:
    order: list[str] = []
    store = _FakeStore(log=order)
    reindex.run_shadow_reindex(
        store,  # type: ignore[arg-type]
        active_alias="active",
        shadow_alias="shadow",
        build_shadow=lambda: order.append("build"),
    )
    assert order == ["build", "flip"]  # 影子全量就绪后才允许切换


def test_flip_failure_rolls_back_to_old_alias() -> None:
    events: list[str] = []
    store = _FakeStore(fail_flip=True)
    out = _run(store, events)
    assert out.applied is False and out.rolled_back is True
    assert out.active_alias == "active" and out.error is not None
    assert store.calls == [("active", "shadow"), ("shadow", "active")]  # 切换失败→回滚旧别名
    assert store.current == "active"


def test_rollback_failure_is_loud_not_silent() -> None:
    events: list[str] = []
    store = _FakeStore(fail_flip=True, fail_rollback=True)
    with pytest.raises(ProviderError):
        _run(store, events)  # 回滚也失败=别名悬空，必须 fail-loud 上浮


def test_same_alias_rejected() -> None:
    store = _FakeStore()
    with pytest.raises(ValueError):
        reindex.run_shadow_reindex(  # type: ignore[arg-type]
            store, active_alias="x", shadow_alias="x", build_shadow=lambda: None
        )


def _pipeline(store: MemoryVectorStore) -> IngestPipeline:
    tmp = Path(tempfile.mkdtemp())
    return IngestPipeline(
        DevHashEmbedding(),
        store,
        FileSystemObjectStore(str(tmp / "obj")),
        PgQueueMq(),
    )


def test_pipeline_shadow_rebuild_reachable_on_real_store() -> None:
    """端到端：种子文档→清单重放→切换成功；证明 shadow_rebuild 用真实 MemoryVectorStore 可达且幂等。"""
    store = MemoryVectorStore()
    pipe = _pipeline(store)
    pipe.process_document(
        Document(doc_id="d1", version=1, domain="hr", acl_tags=["hr"], doc_type="policy", raw_text="人事制度正文一段。")
    )
    before = store.count()
    out = pipe.shadow_rebuild(active_alias="search_active", shadow_alias="search_shadow")
    assert out.applied is True and out.active_alias == "search_shadow"
    assert store.count() == before  # 重放同 chunk_id→upsert 幂等，不重复入库


class _RecordingStore(MemoryVectorStore):
    def __init__(self) -> None:
        super().__init__()
        self.swap_calls: list[tuple[str, str]] = []

    def alias_swap(self, old: str, new: str) -> None:
        self.swap_calls.append((old, new))
        super().alias_swap(old, new)


class _RaisingStore(MemoryVectorStore):
    def alias_swap(self, old: str, new: str) -> None:
        raise RuntimeError("alias backend down")


def test_revert_alias_flips_back_to_previous() -> None:
    """§10「一键切回旧别名」稳态入口：把搜索别名从新版本重指回 72h 保留的上一版本（restore.md 指向此）。"""
    store = _RecordingStore()
    _pipeline(store).revert_alias(from_alias="search_shadow", to_alias="search_active")
    assert store.swap_calls == [("search_shadow", "search_active")]


def test_revert_alias_rejects_same_alias() -> None:
    store = _RecordingStore()
    with pytest.raises(ValueError):
        _pipeline(store).revert_alias(from_alias="x", to_alias="x")
    assert store.swap_calls == []  # 同名校验在触库之前


def test_revert_alias_error_is_loud_not_swallowed() -> None:
    """store 抛错必须原样上抛（fail-loud）——回退索引失败不得静默吞掉。"""
    with pytest.raises(RuntimeError, match="alias backend down"):
        _pipeline(_RaisingStore()).revert_alias(from_alias="search_shadow", to_alias="search_active")
