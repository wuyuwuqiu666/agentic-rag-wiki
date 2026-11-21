"""B 区 wiki 编译 + 回写块合成（F2：回写 ACL=全部源块交集，空则整页收窄）。"""

from __future__ import annotations

from src.platform.types import CanonicalEvent, Chunk, WikiPage, WikiSection


def compile_draft(event: CanonicalEvent, sources: list[Chunk], title: str) -> WikiPage:
    """从源块编译草稿页：ACL 交集合成 + 溯源不变（每节记 source_chunk_ids）。"""
    acl = _intersect_acl(sources, fallback=event.acl_tags)
    body = "\n".join(c.text for c in sources)
    section = WikiSection(heading=title, body=body, source_chunk_ids=[c.chunk_id for c in sources])
    return WikiPage(
        page_id=f"wiki/{event.domain}/{event.doc_id}",
        domain=event.domain,
        title=title,
        status="draft",
        sections=[section],
        source_doc_ids=[event.doc_id],
        acl_tags=acl,
    )


def build_writeback(page: WikiPage) -> list[Chunk]:
    """发布后把 wiki 页合成高权威回写块（authority=wiki），经 IngestionPort 入 A 区索引。"""
    chunks: list[Chunk] = []
    for i, sec in enumerate(page.sections):
        chunks.append(
            Chunk(
                chunk_id=f"{page.page_id}#{i}",
                doc_id=page.page_id,
                doc_version=page.version,
                seq=i,
                text=sec.body,
                breadcrumb=[sec.heading],
                acl_tags=list(page.acl_tags),  # 已是交集（F2）
                authority="wiki",
                token_count=len(sec.body) // 4,
            )
        )
    return chunks


def _intersect_acl(sources: list[Chunk], fallback: list[str]) -> list[str]:
    if not sources:
        return list(fallback) or ["restricted"]
    acc: set[str] | None = None
    for c in sources:
        tags = set(c.acl_tags)
        acc = tags if acc is None else acc & tags
    result = sorted(acc or set())
    # 交集为空=可见人群收窄到无人：整页打 restricted，不放行全量
    return result or ["restricted"]
