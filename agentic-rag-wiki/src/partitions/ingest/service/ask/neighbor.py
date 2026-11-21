"""FR-ING-7 邻居回取（检索期上下文重建）：命中块按 prev/next 拉相邻块补全，替代大 overlap。

口径出处：FR-ING-7「overlap 默认 0，检索期 prev/next 邻居回取重建上下文（Chroma/Pinecone 反 overlap
口径）」；ADR-004「无 CR 的上下文断裂损失由 breadcrumb + 邻居回取部分补偿」；附录 E 术语「邻居回取」。
切块期 `chunker` 早已在 Chunk 上落 `prev`/`next` 链（M4-l），但检索侧此前**从不消费**=写而不读的孤立缝
（同 trace_id 死字段类）。本模块补齐消费端：对 rerank 选中的 top-n，按链从**已召回候选池**取其前后邻块、
去重后并入生成上下文，受 C5 上下文 token 预算约束。

ACL 安全：邻居只从 `pool`（=recall 结果，已过 pre-filter ACL 门）取，绝不旁路取未授权块；不在池内的邻块
不臆造（跨 collection 真·按 id 取正文需 `fetch(ids)` 端口原语=放行档，本 dev 档不扩冻结端口）。
纯函数、确定性（同输入同输出），可对账可单测。
"""

from __future__ import annotations

from src.platform.types import Chunk


def expand_neighbors(
    hits: list[Chunk],
    pool: list[Chunk],
    *,
    max_added_tokens: int,
) -> list[Chunk]:
    """命中块保序在前，逐个按 prev→next 从候选池补邻块，累计补入 token 不越 `max_added_tokens`。

    去重：命中块自身、已补入的邻块不重复。池内取不到（未召回/跨域未授权）即跳过，不臆造。
    返回供喂 LLM 的生成上下文（同时作为可引用证据集——邻块系真实同源块、已过 ACL）。
    """
    pool_by_id = {c.chunk_id: c for c in pool}
    result: list[Chunk] = list(hits)
    included = {c.chunk_id for c in hits}
    added_tokens = 0
    for hit in hits:
        for nid in (hit.prev, hit.next):  # 先补前驱再补后继，确定性顺序
            if nid is None or nid in included:
                continue
            neighbor = pool_by_id.get(nid)
            if neighbor is None:
                continue  # 邻块不在已召回池（未命中检索/跨 ACL）→ 不旁路取
            if added_tokens + neighbor.token_count > max_added_tokens:
                continue  # C5 上下文预算：越限的邻块跳过（后续更小邻块仍可补入）
            result.append(neighbor)
            included.add(nid)
            added_tokens += neighbor.token_count
    return result
