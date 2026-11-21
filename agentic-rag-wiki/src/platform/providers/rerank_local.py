"""RerankPort 执行档主档：本地 bge-reranker-v2-m3（ADR-004）。惰性 import，缺失 fail-loud。"""

from __future__ import annotations

from src.platform.providers.errors import ProviderNotConfigured
from src.platform.types import Chunk


class LocalRerank:
    def __init__(self, model_id: str = "bge-reranker-v2-m3") -> None:
        self._model_id = model_id
        self._model: object | None = None

    @property
    def model_id(self) -> str:
        return self._model_id

    def _load(self) -> object:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover
                raise ProviderNotConfigured("本地 rerank 需 sentence-transformers（NFR-6）") from exc
            self._model = CrossEncoder(self._model_id)
        return self._model

    def rerank(self, query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]:
        model = self._load()
        pairs = [(query, c.text) for c in chunks]
        scores = model.predict(pairs)  # type: ignore[attr-defined]
        order = sorted(range(len(chunks)), key=lambda i: -float(scores[i]))
        return [chunks[i] for i in order[:top_n]]
