"""A 区切块（FR-ING-7）：判型与执行分离——先出节点段决议，再确定性重放。

切块只产结构块（无向量）；向量由 pipeline 经 EmbeddingPort 填。决议记录=ChunkDecision（D-1 归档于 objectstore），
其判型/判级按**节点段**记录（ADR-010）：一份文档里 text 要聚合、table 要原子、code 要函数级，doc 级单值判型装不下。
本模块含 D-2 块型档位（plan/apply 的 table 退化与 code 函数级）、短节并入父节（merge_short）、
F5 输入门限截断（f5_gate）与表格行组父子链（link_table_parents）五个纯函数（可对账、可单测）。
"""

from __future__ import annotations

import re

from src.platform.types import CanonicalDocument, CanonicalNode, Chunk, ChunkDecision, ChunkType, NodeDecision

_CHARS_PER_TOKEN = 4  # 执行档 token 口径（与 canonical/pipeline 同源：len//4）
_TEXT_MIN_TOKEN = 400  # D-2 text 档下界：段落/标题=硬边界，够不到下界仍成块（短节由 merge_short 并父节）
_TEXT_MAX_TOKEN = 800  # D-2 text 档上界：超界才在句末软边界内切
_TABLE_DEGRADE_TOKEN = 2000  # F5：表原子不设上限，但超此阈退化为「表头+行组」
_TABLE_GROUP_TOKEN = 800  # F5：退化行组的每组上限（每组重复表头）
_MIN_TOKEN_MERGE = 100  # D-2：<100 token 短节并入同型父节，消碎片噪声
_F5_DOWNSTREAM_LIMIT_TOKEN = 8192  # F5：最紧下游模型 input 上界（本地 reranker，与 Chunk.input_token_limit 同源）

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")
_CODE_START = re.compile(r"^(?:def |class |async def |@|\w+\s*\(|(?:public|private|protected|func|fn) )")

# 结构节点 kind → 块型：第①级规则档的映射表（未知型回落 text，不臆造结构）
_KIND_TO_CHUNK_TYPE: dict[str, ChunkType] = {"table": "table", "code": "code"}


def _tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def plan(canonical: CanonicalDocument) -> ChunkDecision:
    """三级漏斗的规则档输出：按节点形状出**逐段**决议（text 聚合、table/code 各自独立段）。

    段落/标题/列表=硬边界，可聚合到 text 档上界为止；table/code 永不与散文同段（同段＝把原子结构块拆散
    或让表格污染正文块）。判级一律 decider="rule"——第②③级（版面小模型/LLM few-shot）需真供给＝放行档。
    """
    decisions: list[NodeDecision] = []
    pending: list[str] = []
    acc = 0

    def flush_text() -> None:
        if pending:
            decisions.append(NodeDecision(node_ids=list(pending), chunk_type="text"))
            pending.clear()

    for node in canonical.flatten():
        chunk_type = _KIND_TO_CHUNK_TYPE.get(node.kind, "text")
        if chunk_type != "text":
            flush_text()
            decisions.append(NodeDecision(node_ids=[node.node_id], chunk_type=chunk_type))
            continue
        size = _tokens(node.text)
        # 到档位下界即在硬边界收束（块落在 400–800 token 带内），上界为不可越的硬顶
        if pending and (acc >= _TEXT_MIN_TOKEN or acc + size > _TEXT_MAX_TOKEN):
            flush_text()
            acc = 0
        pending.append(node.node_id)
        acc += size
    flush_text()
    return ChunkDecision(profile="rule_structure_v1", decisions=decisions, doc_token_count=canonical.token_count)


def apply(canonical: CanonicalDocument, decision: ChunkDecision) -> list[Chunk]:  # noqa: C901
    """对决议的确定性重放：同输入必得同输出（可复算、可对账）。

    块型各按 D-2 档位落地：text 超上界才句内切；table 原子、超 F5 退化阈切「表头+行组」且每组重复表头
    （行组的父子关系经 `group` 标记，由 link_table_parents 在最终编号后回填 parent_chunk_id）；
    code 以函数/类为最小单元不跨切。
    """
    by_id = {n.node_id: n for n in canonical.flatten()}
    chunks: list[Chunk] = []
    for seg in decision.decisions:
        nodes = [by_id[nid] for nid in seg.node_ids if nid in by_id]
        if not nodes:
            continue  # 决议指向不存在的节点=重放输入不一致，由对账/B5 侧暴露，此处不臆造正文
        if seg.chunk_type == "table":
            chunks.extend(_table_chunks(canonical, nodes[0], seg))
        elif seg.chunk_type == "code":
            chunks.extend(_code_chunks(canonical, nodes[0], seg))
        else:
            chunks.extend(_text_chunks(canonical, nodes, seg))
    _renumber(chunks)
    return chunks


def _make(canonical: CanonicalDocument, seq: int, text: str, seg: NodeDecision, nodes: list[CanonicalNode]) -> Chunk:
    return Chunk(
        chunk_id=f"{canonical.doc_id}/{canonical.version}#{seq}",
        doc_id=canonical.doc_id,
        doc_version=canonical.version,
        seq=seq,
        text=text,
        chunk_type=seg.chunk_type,
        breadcrumb=[n.node_id for n in nodes],
        acl_tags=list(canonical.acl_tags),
        token_count=_tokens(text),
        authority="source",
    )


def _text_chunks(canonical: CanonicalDocument, nodes: list[CanonicalNode], seg: NodeDecision) -> list[Chunk]:
    """聚合段=硬边界直出；单节点超 text 档上界才按句末软边界内切（D-2「超档才句内切」）。"""
    if len(nodes) > 1 or _tokens(nodes[0].text) <= _TEXT_MAX_TOKEN:
        text = "\n\n".join(n.text for n in nodes)
        return [_make(canonical, 0, text, seg, nodes)]
    out: list[Chunk] = []
    for piece in _split_to_limit(nodes[0].text, _TEXT_MAX_TOKEN):
        out.append(_make(canonical, len(out), piece, seg, [nodes[0]]))
    return out


def _split_to_limit(text: str, max_token: int) -> list[str]:
    pieces = [p for p in _SENTENCE_SPLIT.split(text) if p]
    groups: list[str] = []
    cur = ""
    for p in pieces:
        if cur and _tokens(cur + p) > max_token:
            groups.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        groups.append(cur)
    return groups or [text]


def _table_chunks(canonical: CanonicalDocument, node: CanonicalNode, seg: NodeDecision) -> list[Chunk]:
    """table 原子优先；超 F5 退化阈按「表头+行组」切，每组重复表头，行组共一个 group 标记供父子链。"""
    whole = _make(canonical, 0, node.text, seg, [node])
    if whole.token_count <= _TABLE_DEGRADE_TOKEN:
        return [whole]
    lines = node.text.splitlines()
    header = lines[0] if lines else node.text
    rows = lines[1:] if len(lines) > 1 else []
    group = f"{canonical.doc_id}/{canonical.version}#node-{node.node_id}"
    groups: list[str] = []
    cur: list[str] = []
    for row in rows:
        if cur and _tokens(header + "\n" + "\n".join([*cur, row])) > _TABLE_GROUP_TOKEN:
            groups.append((header + "\n" + "\n".join(cur)).strip())
            cur = [row]
        else:
            cur.append(row)
    if cur:
        groups.append((header + "\n" + "\n".join(cur)).strip())
    chunks = [_make(canonical, i, text, seg, [node]) for i, text in enumerate(groups or [node.text])]
    for i, c in enumerate(chunks):
        c.extra["group"] = group
        c.extra["group_index"] = i
    return chunks


def _code_chunks(canonical: CanonicalDocument, node: CanonicalNode, seg: NodeDecision) -> list[Chunk]:
    """code 以函数/类为最小单元不跨切；无函数边界（单块脚本）整体成块，由 F5 门限兜底。"""
    blocks: list[str] = []
    cur: list[str] = []
    for line in node.text.splitlines():
        if cur and _CODE_START.match(line):
            blocks.append("\n".join(cur).strip())
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur).strip())
    blocks = [b for b in blocks if b]
    if not blocks:
        blocks = [node.text]
    return [_make(canonical, i, b, seg, [node]) for i, b in enumerate(blocks)]


def link_table_parents(chunks: list[Chunk]) -> list[Chunk]:
    """表格退化父子链：同 group 的首块为父，其余行组块 `parent_chunk_id` 指向它，然后清掉临时标记。

    必须在 `merge_short`/重编号之后调用——chunk_id 是按序号派生的，先链会在重编号后指向不存在的 id。
    `Chunk.parent_chunk_id` 由此第一次有了真实消费者（此前 gated-null：doc 级决议表达不出「一张表的行组」）。
    """
    first: dict[str, str] = {}
    for c in chunks:
        group = c.extra.get("group")
        if group and group not in first:
            first[group] = c.chunk_id
    for c in chunks:
        group = c.extra.pop("group", None)
        index = c.extra.pop("group_index", None)
        if group is None:
            continue
        parent = first.get(group)
        if index and parent and parent != c.chunk_id:
            c.parent_chunk_id = parent
    return chunks


def merge_short(chunks: list[Chunk], *, min_token: int = _MIN_TOKEN_MERGE) -> list[Chunk]:
    """D-2 短节并入父节：<min_token 的块并入前一**同型**块（确定性重编号、重链 prev/next）。

    跨型不并（table/code 结构块保原子）；带 group 标记的表格行组不参与并入（并组＝破坏「每组重复表头」的退化口径）。
    """
    merged: list[Chunk] = []
    for c in chunks:
        prev = merged[-1] if merged else None
        mergeable = c.token_count < min_token and "group" not in c.extra
        if prev is not None and prev.chunk_type == c.chunk_type and mergeable and "group" not in prev.extra:
            prev.text = f"{prev.text}\n\n{c.text}"
            prev.breadcrumb = [*prev.breadcrumb, *c.breadcrumb]
            prev.token_count = _tokens(prev.text)
            continue
        merged.append(c.model_copy(deep=True))
    if len(merged) > 1 and merged[0].token_count < min_token and merged[1].chunk_type == merged[0].chunk_type:
        head = merged.pop(0)
        merged[0].text = f"{head.text}\n\n{merged[0].text}"
        merged[0].breadcrumb = [*head.breadcrumb, *merged[0].breadcrumb]
        merged[0].token_count = _tokens(merged[0].text)
    _renumber(merged)
    return merged


def f5_gate(chunks: list[Chunk], *, downstream_limit: int = _F5_DOWNSTREAM_LIMIT_TOKEN) -> list[str]:
    """F5 输入门限：块 token 超下游模型 input 上界→截断到阈并打 extra 标记，返回被截断 chunk_id 供审计。

    口径=NFR-8 fail-loud：截断必须可见（调用方据返回 id 发审计），**不得静默丢尾**。text 档受上界约束、
    table 退化到行组、code 按函数切，正常路径罕见触发；仅病态超长单函数/无边界表单元命中，作兜底保下游不越界。
    """
    truncated: list[str] = []
    keep = downstream_limit * _CHARS_PER_TOKEN
    for c in chunks:
        if c.token_count > downstream_limit:
            c.text = c.text[:keep]
            c.token_count = _tokens(c.text)
            c.extra["f5_truncated"] = True
            truncated.append(c.chunk_id)
    return truncated


def _link(chunks: list[Chunk]) -> None:
    for i, c in enumerate(chunks):
        c.prev = chunks[i - 1].chunk_id if i > 0 else None
        c.next = chunks[i + 1].chunk_id if i + 1 < len(chunks) else None


def _renumber(chunks: list[Chunk]) -> None:
    for seq, c in enumerate(chunks):
        c.seq = seq
        c.chunk_id = f"{c.doc_id}/{c.doc_version}#{seq}"
    _link(chunks)
