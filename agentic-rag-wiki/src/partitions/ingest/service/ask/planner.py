"""检索规划器（FR-ASK-1，dev 档纯逻辑）：分解→扩写查询再检索→跨源核对，步数预算 ≤max_hops。

`expand_query` 从已命中块里抽问题未覆盖的、跨块高频的 bigram 桥接词补进查询（确定性、与 policy 词面口径一致），
驱动下一跳向相邻证据收敛。回路是否续跑（低置信/无新证据/步数预算）由 `GraphPort.run_while` 的 stop 谓词
在 A 区 runtime 判定；真·LangGraph 条件边/interrupt/checkpoint 走 GraphPort 实现（M 后续换档）。本模块只出扩写、零 IO。
"""

from __future__ import annotations

from src.partitions.ingest.service.ask import policy
from src.platform.types import Chunk


def expand_query(question: str, chunks: list[Chunk], *, max_terms: int = 6) -> str:
    """把命中块中、问题尚未覆盖且跨块高频的桥接词补进查询，驱动下一跳向相邻证据收敛。"""
    q_terms = policy.tokens(question)
    counts: dict[str, int] = {}
    for c in chunks:
        for tok in set(policy.tokens(c.text)):
            if tok in q_terms:
                continue
            counts[tok] = counts.get(tok, 0) + 1  # 出现在越多块=越强桥接信号
    bridge = sorted(counts, key=lambda t: (-counts[t], t))[:max_terms]
    return question if not bridge else question + " " + " ".join(bridge)
