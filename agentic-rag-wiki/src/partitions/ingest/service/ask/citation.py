"""引用校验器（FR-ASK-2，P2：校验是代码不是文规）：答案每个事实句必须挂 chunk 级出处。

无 `[chunk_id]` 标记、或标记不在本轮召回块集合内的句子=不可信出处，一律拦截（从答案中剔除并计账）。
全部句被拦→无接地依据→上层按拒答处理（FR-ASK-3）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MARKER = re.compile(r"\[([^\[\]\s]+)\]")
_SENT = re.compile(r"[。！？!?；;\n]")


@dataclass
class CitationReport:
    text: str = ""
    used_chunk_ids: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return bool(self.used_chunk_ids)


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT.split(text)]
    return [p for p in parts if p]


def enforce(text: str, valid_ids: set[str]) -> CitationReport:
    """逐句校验出处：保留挂了合法 chunk 标记的句子，按首次出现去重收集 used_ids，其余入 dropped。"""
    kept: list[str] = []
    used: list[str] = []
    dropped: list[str] = []
    for sentence in split_sentences(text):
        marks = [m for m in _MARKER.findall(sentence) if m in valid_ids]
        if not marks:
            dropped.append(sentence)
            continue
        kept.append(sentence)
        for m in marks:
            if m not in used:
                used.append(m)
    return CitationReport(text="。".join(kept) + ("。" if kept else ""), used_chunk_ids=used, dropped=dropped)
