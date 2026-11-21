"""U5/§5「源连接器（SourcePort）」契约缝（离线代码路径）。

U5 承诺"新增 SharePoint 连接器 → 实现 repo 层源接口 + types 中已有契约 → 不触碰 service/runtime"，
§5 目录树亦点名 `repo/source`「源连接器（SourcePort）」，C4 称"MCP 连接器为 repo/source 的一种实现"——
但此前 src 内**根本无 SourcePort 契约、连接器开发者无处实现**（典型"声称有接口、实则零文件"）。
本用例锁定收口后的契约缝：①`SourcePort` 是 runtime_checkable Protocol、`DevSource` 结构满足它；
②`IngestPipeline.pull` 先存量后增量地消费一个源（B0/B1 门不旁路）；③增量 delete 精确清除存量已入库块。
真实连接器（OA/网盘/Git/SharePoint 读取端）=放行项㉒，此处用内存档 `DevSource` 演练、不冒充真实调度。
"""

from __future__ import annotations

from pathlib import Path

from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.repo.source import DevSource, SourcePort
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.platform.providers.bm25_dev import Bm25Index
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import ChangeEvent, Document


def _body(tag: str) -> str:
    return f"# {tag}标题\n\n" + "\n\n".join(
        f"{tag} 段落 {i} 内容足够长以形成多个可检索块供源连接器消费链校验。" for i in range(6)
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
    return pipeline, vs, inv


def test_devsource_structurally_satisfies_sourceport() -> None:
    src = DevSource(documents=[Document(doc_id="a", raw_text=_body("A"))])
    assert isinstance(src, SourcePort)  # runtime_checkable 结构契约在位


def test_pull_ingests_backlog_then_applies_incremental(tmp_path: Path) -> None:
    pipeline, vs, inv = _pipeline(tmp_path)
    src = DevSource(
        documents=[Document(doc_id="a", domain="hr", acl_tags=["hr"], raw_text=_body("A"))],
        changes=[],
    )
    result = pipeline.pull(src)
    assert result["ingested"] == 1
    assert vs.all_ids()  # 存量经 pull 真实入库
    assert inv.chunk_ids_for("a")  # 清单账登记


def test_pull_incremental_delete_removes_backlogged_doc(tmp_path: Path) -> None:
    pipeline, vs, inv = _pipeline(tmp_path)
    # 存量摄取阶段：两份文档入库，记下 drop 的真实 chunk_id。
    backlog = DevSource(
        documents=[
            Document(doc_id="keep", domain="hr", acl_tags=["hr"], raw_text=_body("K")),
            Document(doc_id="drop", domain="hr", acl_tags=["hr"], raw_text=_body("D")),
        ],
        changes=[],
    )
    assert pipeline.pull(backlog)["ingested"] == 2
    keep_ids = set(inv.chunk_ids_for("keep"))
    drop_ids = set(inv.chunk_ids_for("drop"))
    assert keep_ids and drop_ids
    assert drop_ids <= set(vs.all_ids())

    # 增量阶段：仅一条 delete 变更经 B0/B1 入口落地，精确清除 drop。
    inc = DevSource(documents=[], changes=[ChangeEvent(doc_id="drop", kind="delete")])
    result = pipeline.pull(inc)
    assert result["ingested"] == 0
    assert result["change_deleted"] >= 1

    after = set(vs.all_ids())
    assert after.isdisjoint(drop_ids)  # 增量删除精确清掉 drop 的块
    assert keep_ids <= after  # keep 不受波及
    assert not inv.chunk_ids_for("drop")  # 删后清账→账实相符（对账零孤儿）
