"""FR-ING-4 对账报告落盘 + 一键补偿（追平按清单重拉 objectstore 正文重放，离线代码路径）。

补拉正文来源=CanonicalEvent 清单指针（store_key）+ objectstore 正文，与 FR-ING-6"事件保留期外按清单重拉"同一路径；
正文缺失的真孤儿不臆造、留 B5 背离债。真实 PG↔Milvus 分钟级背离传感属双存储实机=放行项。
"""

from __future__ import annotations

from pathlib import Path

from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.partitions.ingest.service import reconcile
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import CanonicalEvent, Document

_BIG = "# 标题\n\n" + "\n\n".join(f"段落 {i} 内容足够长以形成多个可检索块。" for i in range(6))


# —— 纯判定 ——
def test_diff_and_divergence() -> None:
    missing, extra = reconcile.diff({"a", "b", "c"}, {"a", "x"})
    assert missing == ["b", "c"]  # 升序
    assert extra == ["x"]
    assert reconcile.divergence_pct(2, 100) == 2.0
    assert reconcile.divergence_pct(5, 0) == 0.0  # 空库不告警


def test_render_report_lists_missing_and_clean() -> None:
    report = reconcile.render_report(expected_count=3, actual_count=1, missing=["b", "c"], extra=[], generated_at=1700.0)
    assert "缺失源块：2" in report and "b" in report and "背离率" in report
    clean = reconcile.render_report(expected_count=1, actual_count=1, missing=[], extra=[], generated_at=1700.0)
    assert "账实相符" in clean


def test_render_report_separates_authority_page_blocks_from_source_blocks() -> None:
    """M4-cd①：权威页缺块单列一节并给页归属——处置＝B 区重编译，不是"重拉正文"，口径要写在产物上。"""
    report = reconcile.render_report(
        expected_count=4,
        actual_count=2,
        missing=["hrdoc/1#0"],
        extra=[],
        generated_at=1700.0,
        writeback_missing={"wiki/hr/hrdoc": ["wiki/hr/hrdoc#0"]},
    )
    assert "缺失源块：1" in report and "缺失权威页块（属已发布页，另列）：1" in report
    assert "页 `wiki/hr/hrdoc`" in report and "`wiki/hr/hrdoc#0`" in report
    assert "不重拉正文" in report  # 处置口径随报告落地，不只活在代码注释里


def _pipeline(tmp_path: Path) -> tuple[IngestPipeline, MemoryVectorStore, Path]:
    rec_dir = tmp_path / "reconcile"
    vs = MemoryVectorStore()
    pipeline = IngestPipeline(
        DevHashEmbedding(dim=8),
        vs,
        FileSystemObjectStore(str(tmp_path / "obj")),
        PgQueueMq(),
        inventory=CanonicalInventory(),
        reconcile_dir=rec_dir,
    )
    return pipeline, vs, rec_dir


def test_compensate_reindexes_lost_doc_and_writes_report(tmp_path: Path) -> None:
    pipeline, vs, rec_dir = _pipeline(tmp_path)
    pipeline.process_document(Document(doc_id="lost", version=1, domain="hr", acl_tags=["hr"], raw_text=_BIG))
    lost_ids = [cid for cid in vs.all_ids() if cid.startswith("lost/")]
    assert lost_ids  # 初灌有块
    for cid in lost_ids:
        vs.delete([cid])  # 模拟索引侧丢块（清单仍记=expected）

    summary = pipeline.compensate(now=1700000000.0)

    assert summary["missing"] == len(lost_ids) and summary["backfilled"] == 1
    assert pipeline.reconcile()["missing"] == 0  # 重放后账实相符（chunk_id 确定性→同块回填）
    files = list(rec_dir.glob("reconcile-*.md"))
    assert files and lost_ids[0] in files[0].read_text(encoding="utf-8")  # 报告含待补偿块 id


def test_backfill_preserves_acl_from_inventory_pointer(tmp_path: Path) -> None:
    """补偿重拉须按清单指针重建 ACL（非臆造）：越权用户检索仍不可见补拉后的块。"""
    pipeline, vs, _ = _pipeline(tmp_path)
    pipeline.process_document(Document(doc_id="hrdoc", version=1, domain="hr", acl_tags=["hr"], raw_text=_BIG))
    for cid in [c for c in vs.all_ids() if c.startswith("hrdoc/")]:
        vs.delete([cid])
    pipeline.compensate(now=1700000000.0, write_report=False)
    recovered = [c for c in vs.all_ids() if c.startswith("hrdoc/")]
    assert recovered and all(vs._by_id[c].acl_tags == ["hr"] for c in recovered)  # type: ignore[attr-defined]


def test_lost_body_is_not_fabricated(tmp_path: Path) -> None:
    """清单记了块、objectstore 无正文=真孤儿：不臆造重放、计入背离留 B5，backfilled=0。"""
    inv = CanonicalInventory()
    vs = MemoryVectorStore()
    pipeline = IngestPipeline(
        DevHashEmbedding(dim=8),
        vs,
        FileSystemObjectStore(str(tmp_path / "obj")),
        PgQueueMq(),
        inventory=inv,
    )
    inv.record(CanonicalEvent(doc_id="ghost", version=1, domain="hr", store_key="ghost/1", acl_tags=["hr"]), ["ghost/1#0"])
    summary = pipeline.compensate(now=1700000000.0, write_report=False)
    assert summary["missing"] == 1 and summary["backfilled"] == 0  # 无正文→不臆造
    assert not vs.all_ids()
