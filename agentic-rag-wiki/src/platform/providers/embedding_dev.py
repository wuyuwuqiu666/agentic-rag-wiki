"""EmbeddingPort dev 档（dev_hash）：确定性哈希向量，零依赖跑通闭环。

注意：这是**流水线接线**用的占位向量，不代表任何检索质量（AGENTS §8 诚实账）。
执行档主档=embedding_local（Qwen3-Embedding-0.6B），云档=加速换插位。
"""

from __future__ import annotations

import hashlib
import math


class DevHashEmbedding:
    def __init__(self, dim: int = 256, input_token_limit: int = 8192) -> None:
        self._dim = dim
        self._limit = input_token_limit
        self._model_id = "dev_hash"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def input_token_limit(self) -> int:
        return self._limit

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec: list[float] = []
        seed = text.encode("utf-8")
        counter = 0
        while len(vec) < self._dim:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            vec.extend((b / 127.5) - 1.0 for b in digest)
            counter += 1
        vec = vec[: self._dim]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
