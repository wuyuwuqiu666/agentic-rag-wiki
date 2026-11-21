"""LexicalPort dev 档（bm25_dev）：进程内 Okapi-BM25，零依赖跑通 FR-ASK-5 漏斗词法路。

分词默认 ASCII 词 + CJK 字符 bigram；jieba 若可用则优先中文切词（可选增强，缺失即回退，
不硬依赖）。打分质量=dev 占位，非检索质量基线；执行档换真实分词/embedding 只换本实现（G5）。
"""

from __future__ import annotations

import math
import re
from typing import Any

from src.platform.types import Chunk

_ASCII = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]+")


def _jieba() -> Any:  # pragma: no cover - 可选依赖，缺包即回退
    try:
        import jieba  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

        return jieba
    except ImportError:
        return None


class Bm25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._by_id: dict[str, Chunk] = {}
        self._tokens: dict[str, list[str]] = {}
        self._df: dict[str, int] = {}  # term → 含该 term 的文档数
        self._total_len = 0
        self._jieba = _jieba()

    def index(self, chunks: list[Chunk]) -> int:
        n = 0
        for c in chunks:
            if not c.chunk_id:
                continue
            if c.chunk_id in self._by_id:
                self._remove(c.chunk_id)
            self._by_id[c.chunk_id] = c
            toks = self._tokenize(c.text)
            self._tokens[c.chunk_id] = toks
            for term in set(toks):
                self._df[term] = self._df.get(term, 0) + 1
            self._total_len += len(toks)
            n += 1
        return n

    def delete(self, chunk_ids: list[str]) -> int:
        removed = 0
        for cid in chunk_ids:
            if self._remove(cid):
                removed += 1
        return removed

    def search(self, query: str, topk: int, acl_tags: list[str]) -> list[Chunk]:
        allowed = set(acl_tags)
        q_terms = set(self._tokenize(query))
        if not q_terms or not self._by_id:
            return []
        avg_len = (self._total_len / len(self._by_id)) or 1.0
        n_docs = len(self._by_id)
        scored: list[tuple[float, int, Chunk]] = []
        for i, (cid, chunk) in enumerate(self._by_id.items()):
            if not allowed.issuperset(chunk.acl_tags):
                continue
            toks = self._tokens.get(cid, [])
            if not toks:
                continue
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            dl = len(toks)
            for term in q_terms & set(tf):
                idf = math.log(1 + (n_docs - self._df[term] + 0.5) / (self._df[term] + 0.5))
                freq = tf[term]
                score += idf * (freq * (self._k1 + 1)) / (freq + self._k1 * (1 - self._b + self._b * dl / avg_len))
            if score > 0:
                scored.append((score, i, chunk))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [c for _, _, c in scored[:topk]]

    def _remove(self, cid: str) -> bool:
        toks = self._tokens.pop(cid, None)
        if toks is None:
            return False
        for term in set(toks):
            self._df[term] = self._df.get(term, 0) - 1
            if self._df[term] <= 0:
                del self._df[term]
        self._total_len -= len(toks)
        self._by_id.pop(cid, None)
        return True

    def _tokenize(self, text: str) -> list[str]:
        low = text.lower()
        tokens: list[str] = _ASCII.findall(low)
        for run in _CJK.findall(low):
            if self._jieba is not None:
                tokens.extend(t for t in self._jieba.cut(run) if t.strip())
            else:
                tokens.extend(_bigram(run))
        return tokens


def _bigram(run: str) -> list[str]:
    if len(run) == 1:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]
