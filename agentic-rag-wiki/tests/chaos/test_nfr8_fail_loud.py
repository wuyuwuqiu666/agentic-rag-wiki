"""M4-b NFR-8 fail-loud 验证（§13 M4：切本地档复跑，显存/token 越界必须报警而非静默通过）。

全离线（不加载真模型）：断言"越界即抛、管道不吞异常"的不变式，非吞吐质量数字（吞吐跌幅实测属放行项）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.embedding_local import LocalEmbedding
from src.platform.providers.errors import ProviderError, ProviderNotConfigured, VramBudgetExceeded
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Chunk


def _pipeline(embedding: object, batch_safe: int = 8) -> IngestPipeline:
    tmp = Path(tempfile.mkdtemp())
    return IngestPipeline(
        embedding,  # type: ignore[arg-type]
        MemoryVectorStore(),
        FileSystemObjectStore(str(tmp / "obj")),
        PgQueueMq(),
        batch_safe=batch_safe,
    )


def _chunk(cid: str, tokens: int = 10) -> Chunk:
    return Chunk(chunk_id=cid, doc_id="d", doc_version=1, seq=0, text="x" * 4, token_count=tokens, acl_tags=["hr"])


def test_local_embedding_batch_overflow_raises_not_silent() -> None:
    """batch 越显存安全上界→VramBudgetExceeded（加载模型前即抛，不静默落系统内存）。"""
    with pytest.raises(VramBudgetExceeded):
        LocalEmbedding(batch_safe=8).embed([f"文本{i}" for i in range(9)])


def test_local_embedding_missing_deps_fails_loud() -> None:
    """本地档依赖缺失→ProviderNotConfigured，绝不静默回退 dev 档（NFR-8）。"""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        with pytest.raises(ProviderNotConfigured):
            LocalEmbedding(batch_safe=32).embed(["一段文本"])
    else:  # pragma: no cover - 本机已装依赖则缺失路径不适用
        pytest.skip("本机已装 sentence-transformers，缺失路径不适用")


def test_pipeline_propagates_vram_overflow_not_swallowed() -> None:
    """provider 对超限 batch 抛 Vram：管道无 try/except，必须原样上抛而非继续索引（静默通过）。"""

    class StrictBatchEmbedding(DevHashEmbedding):
        def embed(self, texts: list[str]) -> list[list[float]]:
            if len(texts) > 2:
                raise VramBudgetExceeded(batch=len(texts), safe_batch=2)
            return super().embed(texts)

    pipeline = _pipeline(StrictBatchEmbedding(dim=8), batch_safe=8)  # 管道按 8 批发，provider 只吃 2→必越界
    with pytest.raises(VramBudgetExceeded):
        pipeline._embed_and_index([_chunk("d#0"), _chunk("d#1"), _chunk("d#2")])


def test_pipeline_rejects_token_overflow_fail_loud() -> None:
    """块 token 越 embedding 输入上界→ProviderError 抛出（禁静默截断/降速）。"""
    pipeline = _pipeline(DevHashEmbedding(dim=8, input_token_limit=100))
    with pytest.raises(ProviderError):
        pipeline._embed_and_index([_chunk("d#0", tokens=5000)])
