"""RerankPort dev 档（dev_lexical）：词面重合度打分，零依赖闭环占位。

非检索质量基线；执行档主档=rerank_local（bge-reranker-v2-m3）。
"""

from __future__ import annotations

from src.platform.types import Chunk


class DevLexicalRerank:
    def rerank(self, query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]:
        q_tokens = set(query.split())
        scored = [(self._overlap(q_tokens, c.text), i, c) for i, c in enumerate(chunks)]
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [c for _, _, c in scored[:top_n]]

    @staticmethod
    def _overlap(q: set[str], text: str) -> float:
        if not q:
            return 0.0
        d = set(text.split())
        return len(q & d) / len(q)
