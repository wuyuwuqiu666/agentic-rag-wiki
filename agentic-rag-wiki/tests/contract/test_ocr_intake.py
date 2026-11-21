"""FR-ING-8 三态探测分流 + OCR 质量门（离线代码路径）。

锁定可机检的三条不变式：①页面级分流谓词（第⓪刀）；②scanned/mixed 走 OCR 旁路、未装配供给即 fail-loud（禁当 digital 静默入库）；
③OCR 空/退化=失败页不进索引并隔离审计。dev=PassthroughOcr，真 OCR 模型/逐页探测/夜窗批调度=放行项（OcrPort 接口不变，G5）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.partitions.ingest.service import intake
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.errors import ProviderError
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import AuditEvent, Document


class _SpyOcr:
    """记录调用次数并返回固定文本（可配空串模拟 OCR 失败）。"""

    def __init__(self, out: str) -> None:
        self._out = out
        self.calls = 0

    def recognize(self, document: Document) -> str:
        self.calls += 1
        return self._out


class _CollectAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


def _pipeline(ocr: object | None, audit: _CollectAudit | None = None) -> tuple[IngestPipeline, MemoryVectorStore]:
    tmp = Path(tempfile.mkdtemp())
    vs = MemoryVectorStore()
    pipeline = IngestPipeline(
        DevHashEmbedding(dim=8),
        vs,
        FileSystemObjectStore(str(tmp / "obj")),
        PgQueueMq(),
        audit=audit,  # type: ignore[arg-type]
        ocr=ocr,  # type: ignore[arg-type]
    )
    return pipeline, vs


# —— ①纯分流谓词 + 质量门 ——
def test_needs_ocr_only_for_scan_lanes() -> None:
    assert intake.needs_ocr("digital") is False
    assert intake.needs_ocr("scanned") is True
    assert intake.needs_ocr("mixed") is True


def test_quality_gate_blocks_empty_and_short() -> None:
    assert intake.ocr_output_ok("   ") is False  # 纯空白=失败页
    assert intake.ocr_output_ok("识别出的正文") is True
    assert intake.ocr_output_ok("abc", min_chars=5) is False  # 未达阈
    assert intake.ocr_output_ok("abcdef", min_chars=5) is True


def _doc(page_state: str, raw_text: str = "扫描件占位，无文本层") -> Document:
    return Document(doc_id="scan-1", version=1, domain="hr", acl_tags=["hr"], raw_text=raw_text, page_state=page_state)


# —— ②scanned 走 OCR 旁路（缝被真正调用，正文取 OCR 结果）——
def test_scanned_routes_through_ocr_and_indexes() -> None:
    recognized = "# 标题\n\n" + "\n\n".join(f"识别段落 {i} 有足够长度形成检索块。" for i in range(8))
    spy = _SpyOcr(recognized)
    pipeline, vs = _pipeline(spy)
    pipeline.process_document(_doc("scanned"))
    assert spy.calls == 1  # 主车道未直连、确经 OCR 旁路取正文
    assert vs.all_ids()  # 识别正文入索引


def test_digital_never_touches_ocr() -> None:
    spy = _SpyOcr("不该被用到")
    pipeline, _ = _pipeline(spy)
    doc = _doc("digital", raw_text="# 标题\n\n" + "\n\n".join(f"原生段落 {i} 足够长。" for i in range(8)))
    pipeline.process_document(doc)
    assert spy.calls == 0  # digital 主车道，不经 OCR


# —— ③未装配 OCR 供给：scanned 必须 fail-loud，绝不静默当 digital 入库 ——
def test_scanned_without_ocr_supply_fails_loud() -> None:
    pipeline, vs = _pipeline(None)
    with pytest.raises(ProviderError):
        pipeline.process_document(_doc("scanned"))
    assert not vs.all_ids()  # 未静默入库


# —— ③质量门：OCR 产出空=失败页，不进索引并隔离审计 ——
def test_empty_ocr_output_quarantined_not_indexed() -> None:
    audit = _CollectAudit()
    spy = _SpyOcr("")  # 模拟整本扫描识别失败
    pipeline, vs = _pipeline(spy, audit)
    pipeline.process_document(_doc("scanned"))
    assert not vs.all_ids()  # 失败页绝不入索引
    assert any(e.kind == "quarantine" and e.doc_id == "scan-1" for e in audit.events)
