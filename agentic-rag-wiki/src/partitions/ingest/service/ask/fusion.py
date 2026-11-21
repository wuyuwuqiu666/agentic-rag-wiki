"""MRRF 融合（FR-ASK-5）：向量路与 BM25 路按名次倒数融合，免分数校准、默认零调参。

纯函数、零 SDK：只吃 chunk_id 序或 Chunk 序，输出融合序（P1 纯净内核的一部分）。
"""

from __future__ import annotations

from src.platform.types import Chunk


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[str]:
    """多路排名 → 融合 ID 序。score(d)=Σ_路 1/(k+rank)。并列按首次出现名次稳定。"""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    idx = 0
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(cid, idx)
            idx += 1
    return sorted(scores, key=lambda cid: (-scores[cid], first_seen[cid]))


def fuse_lists(
    vector_hits: list[Chunk],
    bm25_hits: list[Chunk],
    k: int = 60,
) -> list[Chunk]:
    """两路 Chunk 结果融合：按 chunk_id 去重（保字段最全的一份），返回 RRF 序 Chunk。"""
    by_id: dict[str, Chunk] = {}
    for hit in [*vector_hits, *bm25_hits]:
        prev = by_id.get(hit.chunk_id)
        if prev is None or (hit.authority == "wiki" and prev.authority != "wiki"):
            by_id[hit.chunk_id] = hit
    order = reciprocal_rank_fusion([[c.chunk_id for c in vector_hits], [c.chunk_id for c in bm25_hits]], k=k)
    return [by_id[cid] for cid in order if cid in by_id]


def prefer_wiki(chunks: list[Chunk]) -> list[Chunk]:
    """FR-WIKI-5 检索优先：已入选相关块中，wiki 权威页排在原文之前（组内稳定保序）。

    只在 top-n 精选后做同层重排，不改相关性判定本身——回写页经人审背书，同等相关时优先呈现。
    """
    wiki = [c for c in chunks if c.authority == "wiki"]
    src = [c for c in chunks if c.authority != "wiki"]
    return wiki + src
