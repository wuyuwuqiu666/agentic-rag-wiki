"""置信度与拒答口径（FR-ASK-3）：集中 App 原内联判定，供多跳/漏斗复用、可机检。

confidence = 覆盖度（命中块数/最小期望）与落地度（问题↔证据词面重合）的均值；
低置信→明示拒答（拒答率进评测而非隐藏）。词面口径为 dev 代理，非模型质量分。
"""

from __future__ import annotations

import re

from src.platform.types import Chunk

_ASCII = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]+")


def tokens(text: str) -> set[str]:
    low = text.lower()
    out: set[str] = set(_ASCII.findall(low))
    for run in _CJK.findall(low):
        if len(run) == 1:
            out.add(run)
        else:
            out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


def grounding(question: str, chunks: list[Chunk]) -> float:
    """问题 token 在证据上下文中的覆盖率（0..1）。"""
    q = tokens(question)
    if not q or not chunks:
        return 0.0
    ctx: set[str] = set()
    for c in chunks:
        ctx |= tokens(c.text)
    return len(q & ctx) / len(q)


def answer_confidence(question: str, ranked: list[Chunk], *, topn_min: int) -> float:
    """覆盖度与落地度均值；无命中=0。"""
    if not ranked:
        return 0.0
    coverage = min(1.0, len(ranked) / max(1, topn_min))
    return round(0.5 * coverage + 0.5 * grounding(question, ranked), 4)


def is_refused(confidence: float, threshold: float) -> bool:
    return confidence < threshold
