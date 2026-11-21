"""VectorStorePort memory 档：进程内字典，零依赖闭环与契约测试基线。

幂等键=chunk_id（upsert 覆盖），检索=余弦相似 + ACL 过滤（chunk 要求标签须全被请求方持有）。
生产双实现（Milvus/pgvector）保留端口，M1 起接入（不回落 pgvector：双实现对账是核心展品）。
"""

from __future__ import annotations

import math

from src.platform.types import Chunk


class MemoryVectorStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Chunk] = {}
        self._aliases: dict[str, str] = {}

    def upsert(self, chunks: list[Chunk]) -> int:
        for c in chunks:
            self._by_id[c.chunk_id] = c
        return len(chunks)

    def delete(self, chunk_ids: list[str]) -> int:
        n = 0
        for cid in chunk_ids:
            if self._by_id.pop(cid, None) is not None:
                n += 1
        return n

    def search(self, vector: list[float], topk: int, acl_tags: list[str]) -> list[Chunk]:
        allowed = set(acl_tags)
        visible = [c for c in self._by_id.values() if allowed.issuperset(c.acl_tags)]
        scored = sorted(visible, key=lambda c: -_cosine(vector, c.vector))
        return scored[:topk]

    def get_chunk(self, chunk_id: str, acl_tags: list[str]) -> Chunk | None:
        """展示读回（出处面板用）：非 VectorStorePort 协议方法、仅内存档提供。

        沿用与 `search` 同一 pre-filter 口径（chunk 标签须全被请求方持有），绝不按 id 旁路 ACL——
        否则给出 chunk_id 即可越权拉取跨域正文（FR-CAP-4 禁 post-filter/越权读）。
        """
        c = self._by_id.get(chunk_id)
        if c is None or not set(acl_tags).issuperset(c.acl_tags):
            return None
        return c

    def alias_swap(self, old: str, new: str) -> None:
        self._aliases[new] = self._aliases.pop(old, new)

    def all_ids(self) -> list[str]:
        return list(self._by_id)

    def count(self) -> int:
        return len(self._by_id)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
