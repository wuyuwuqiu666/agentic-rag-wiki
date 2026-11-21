"""§10 / §8.5 B1：批量删除的软删除回滚点——tombstone 恢复作业（离线代码路径）。

删除落地即在 A 区记可恢复墓碑（正文留 objectstore），保留期内可一键回滚重放（同 chunk_id，upsert 幂等）；
过保留期=超出可回滚窗口、正文真丢=不臆造——二者均不恢复。内存 DeleteLedger=stand-in，真 30 天保留/跨进程持久=PG 放行项②。
"""

from __future__ import annotations

from pathlib import Path

from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.repo.delete_ledger import DeleteLedger
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import ChangeEvent, Document

_BODY = "# 标题\n\n" + "\n\n".join(f"段落 {i} 内容足够长以形成多个可检索块用于删除回滚校验。" for i in range(6))


def _pipeline(tmp_path: Path, *, retain: float = 100.0, guard=None):  # noqa: ANN001 - GuardPort
    vs = MemoryVectorStore()
    inv = CanonicalInventory()
    ledger = DeleteLedger(retain_seconds=retain)
    obj = FileSystemObjectStore(str(tmp_path / "obj"))
    pipeline = IngestPipeline(
        DevHashEmbedding(dim=8),
        vs,
        obj,
        PgQueueMq(),
        inventory=inv,
        delete_ledger=ledger,
        guard=guard,
    )
    return pipeline, vs, inv, obj, ledger


class _CountingGuard:
    """只记次数不自判阈值的 GuardPort stand-in：锁「一次动作只传感一次」（阈值属 platform/breaker 禁改区）。"""

    def __init__(self) -> None:
        self.checks: list[tuple[str, str]] = []

    def check(self, metric: str, value: float, breaker: str, partition: str = "platform") -> None:  # noqa: ARG002
        self.checks.append((metric, breaker))

    def ensure_write_allowed(self, partition: str = "platform") -> None:  # noqa: ARG002
        return None


def test_delete_records_recoverable_tombstone(tmp_path: Path) -> None:
    pipeline, vs, inv, _, ledger = _pipeline(tmp_path)
    pipeline.process_document(Document(doc_id="del", version=1, domain="hr", acl_tags=["hr"], raw_text=_BODY))
    ids = [cid for cid in vs.all_ids() if cid.startswith("del/")]
    assert ids

    pipeline.apply_changes([ChangeEvent(doc_id="del", kind="delete")], now=0.0)

    assert not [cid for cid in vs.all_ids() if cid.startswith("del/")]  # 已从活跃索引移除
    assert pipeline.reconcile()["missing"] == 0  # 清账→删除不算幻影缺失
    assert ledger.stats(0.0)["recoverable"] == 1  # 保留期内可回滚
    assert "del/1#0" not in inv.expected_ids()  # 清单确已 forget（回滚靠 objectstore 正文，非靠清单）


def test_restore_within_retention_reindexes_same_chunk_ids(tmp_path: Path) -> None:
    pipeline, vs, _, _, ledger = _pipeline(tmp_path)
    pipeline.process_document(Document(doc_id="del", version=1, domain="hr", acl_tags=["hr"], raw_text=_BODY))
    before = sorted(cid for cid in vs.all_ids() if cid.startswith("del/"))
    pipeline.apply_changes([ChangeEvent(doc_id="del", kind="delete")], now=0.0)
    assert not [cid for cid in vs.all_ids() if cid.startswith("del/")]

    outcome = pipeline.restore_deletes(now=50.0)  # 保留期内（retain=100）

    assert outcome["restored"] == 1
    assert sorted(cid for cid in vs.all_ids() if cid.startswith("del/")) == before  # 逐 id 复现（幂等重放）
    assert ledger.stats(50.0)["recoverable"] == 0  # 已标记恢复，不再待回滚
    assert pipeline.reconcile()["missing"] == 0  # 回滚后账实相符


def test_restore_after_window_is_not_recoverable(tmp_path: Path) -> None:
    pipeline, vs, _, _, ledger = _pipeline(tmp_path, retain=100.0)
    pipeline.process_document(Document(doc_id="del", version=1, acl_tags=["hr"], raw_text=_BODY))
    pipeline.apply_changes([ChangeEvent(doc_id="del", kind="delete")], now=0.0)

    outcome = pipeline.restore_deletes(now=150.0)  # 超 30 天窗（此处 100s）→超出回滚窗口

    assert outcome["restored"] == 0
    assert outcome["expired_unrecoverable"] == 1
    assert not [cid for cid in vs.all_ids() if cid.startswith("del/")]  # 不臆造找回


def test_restore_skips_when_body_actually_gone(tmp_path: Path) -> None:
    """正文真丢（objectstore 指针失效）→恢复作业跳过、不臆造内容（与补偿同 fail-safe 口径）。

    M4-cn⑧：`recoverable` 只承诺"真能找回"的那一份——窗内但正文已丢的墓碑计入 `body_missing`，
    记进 recoverable 就是虚报可回滚面（声称＞执法）。
    """
    pipeline, vs, _, obj, ledger = _pipeline(tmp_path)
    pipeline.process_document(Document(doc_id="del", version=1, acl_tags=["hr"], raw_text=_BODY))
    pipeline.apply_changes([ChangeEvent(doc_id="del", kind="delete")], now=0.0)
    obj._path("del/1").unlink()  # 模拟正文物理丢失

    outcome = pipeline.restore_deletes(now=10.0)

    assert outcome["restored"] == 0  # 无可重拉正文→不恢复
    assert outcome["recoverable"] == 0  # 窗内也不报"可回滚"（正文不在＝找不回来）
    assert outcome["body_missing"] == 1  # 另有其账：待人工核查的那一份，不混进可回滚面
    assert not [cid for cid in vs.all_ids() if cid.startswith("del/")]
    assert ledger.stats(10.0)["recoverable"] == 1  # 墓碑账本身仍在窗内（账实与可找回是两回事）


def test_whole_doc_delete_restores_all_versions(tmp_path: Path) -> None:
    pipeline, vs, _, _, ledger = _pipeline(tmp_path)
    pipeline.process_document(Document(doc_id="vv", version=1, acl_tags=["hr"], raw_text=_BODY))
    pipeline.process_document(Document(doc_id="vv", version=2, acl_tags=["hr"], raw_text=_BODY))
    before = sorted(cid for cid in vs.all_ids() if cid.startswith("vv/"))
    assert any(v in cid for cid in before for v in ("vv/1#", "vv/2#"))  # 两版本都在

    pipeline.apply_changes([ChangeEvent(doc_id="vv", kind="delete")], now=0.0)  # 无 version=整 doc 全版本删
    assert not [cid for cid in vs.all_ids() if cid.startswith("vv/")]

    outcome = pipeline.restore_deletes(now=10.0)

    assert outcome["restored"] == 2  # 两版本各一条墓碑、各自回滚
    assert sorted(cid for cid in vs.all_ids() if cid.startswith("vv/")) == before


def test_pending_deletes_reports_recoverability_per_tombstone(tmp_path: Path) -> None:
    """墓碑选单是"恢复作业的可回滚面"：窗口内逐条给可恢复、正文在否、剩余秒数；过窗即如实转为不可恢复。"""
    pipeline, _, _, _, _ = _pipeline(tmp_path, retain=100.0)
    for did in ("a", "b"):
        pipeline.process_document(Document(doc_id=did, version=1, domain="hr", acl_tags=["hr"], raw_text=_BODY))
    pipeline.apply_changes(
        [ChangeEvent(doc_id="a", kind="delete"), ChangeEvent(doc_id="b", kind="delete")],
        now=0.0,
    )

    rows = {r["doc_id"]: r for r in pipeline.pending_deletes(now=50.0)}
    assert set(rows) == {"a", "b"}
    assert all(r["recoverable"] and r["has_body"] and r["chunk_ids"] for r in rows.values())
    assert all(r["seconds_left"] == 50.0 for r in rows.values())

    later = {r["doc_id"]: r for r in pipeline.pending_deletes(now=150.0)}
    assert all(not r["recoverable"] for r in later.values())  # 过窗不臆造"还能找回"
    assert all(later[d]["chunk_ids"] for d in ("a", "b"))  # 账目仍可读（供事后核查），只是不可回滚


def test_restore_can_be_scoped_to_one_document(tmp_path: Path) -> None:
    """逐条恢复：只回滚点名的那份，其余墓碑留在账上等下一次决定（不顺手全量重放）。"""
    pipeline, vs, _, _, _ = _pipeline(tmp_path)
    for did in ("a", "b"):
        pipeline.process_document(Document(doc_id=did, version=1, domain="hr", acl_tags=["hr"], raw_text=_BODY))
    pipeline.apply_changes(
        [ChangeEvent(doc_id="a", kind="delete"), ChangeEvent(doc_id="b", kind="delete")],
        now=0.0,
    )

    outcome = pipeline.restore_deletes(doc_id="a", now=10.0)

    assert outcome == {"restored": 1, "recoverable": 1, "body_missing": 0, "expired_unrecoverable": 0}
    assert [cid for cid in vs.all_ids() if cid.startswith("a/")]  # a 回来了
    assert not [cid for cid in vs.all_ids() if cid.startswith("b/")]  # b 仍未恢复
    assert [r["doc_id"] for r in pipeline.pending_deletes(now=10.0)] == ["b"]


def test_restore_senses_b5_exactly_once(tmp_path: Path) -> None:
    """M4-cn⑦「一次动作只传感一次」（承 M4-ci 只读不传感、M4-cj 判门在写侧之前）：

    恢复作业末尾要过 B5 复核账实——内部那次 `reconcile()` 若也传感，同一动作就记了两笔观察；
    B5 硬断档下连续达阈会升 B0 冻结全部写侧，"做一次恢复"被数成"出现两次背离"＝自己把系统往拉闸推。
    """
    guard = _CountingGuard()
    pipeline, _, _, _, _ = _pipeline(tmp_path, guard=guard)
    pipeline.process_document(Document(doc_id="del", version=1, domain="hr", acl_tags=["hr"], raw_text=_BODY))
    pipeline.apply_changes([ChangeEvent(doc_id="del", kind="delete")], now=0.0)
    guard.checks.clear()  # 只看恢复这一步

    outcome = pipeline.restore_deletes(now=10.0)

    assert outcome["restored"] == 1
    assert [m for m, _ in guard.checks].count("reconcile_missing_chunks") == 1
