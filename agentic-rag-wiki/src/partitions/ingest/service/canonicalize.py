"""A 区：源文档 → canonical 归一（F1：正文入 ObjectStorePort，事件只带指针）。

分区只依赖 platform 端口/契约（check_imports 执法）；零 SDK。
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from src.platform.providers import ObjectStorePort
from src.platform.types import CanonicalDocument, CanonicalEvent, CanonicalNode, Document


def canonicalize(
    document: Document,
    store: ObjectStorePort,
    *,
    origin: Literal["ingest", "reconcile_backfill"] = "ingest",
) -> tuple[CanonicalDocument, CanonicalEvent]:
    """把原始 Document 归一为唯一事实源，并把正文落 objectstore、返回指针信封。

    `origin`=reconcile_backfill 标记"按清单重拉补偿"而非新内容（FR-ING-4/6 追平语义），随 fan-out 带到 B 区避免误判为源变更。
    """
    nodes = _to_nodes(document.raw_text)
    body = document.raw_text.encode("utf-8")
    checksum = hashlib.sha256(body).hexdigest()
    key = f"{document.doc_id}/{document.version}"
    store.put(key, body)
    canonical = CanonicalDocument(
        doc_id=document.doc_id,
        version=document.version,
        domain=document.domain,
        doc_type=document.doc_type,
        title=document.title,
        acl_tags=list(document.acl_tags),
        root=nodes,
        token_count=len(document.raw_text) // 4,
        checksum=checksum,
    )
    event = CanonicalEvent(
        doc_id=document.doc_id,
        version=document.version,
        domain=document.domain,
        acl_tags=list(document.acl_tags),
        checksum=checksum,
        store_key=key,
        token_count=canonical.token_count,
        origin=origin,
        doc_type=document.doc_type,
    )
    return canonical, event


NodeKind = Literal["heading", "paragraph", "table", "code", "list"]

_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+•]\s+|\d+[.)、]\s+)")


def _is_table(lines: list[str]) -> bool:
    """规则档表格判据（FR-ING-7 第①级）：≥2 行、逐行带分隔符（| 或制表符），且第二行是表头分隔行或全部同行数。

    宁可少判：只认「整块都是分隔结构」的块，散文里偶然一个 | 不算表格（误判＝把正文当原子表块，检索不可分）。
    """
    if len(lines) < 2:
        return False
    if not all(("|" in ln or "\t" in ln) for ln in lines):
        return False
    if _TABLE_SEP.match(lines[1]):
        return True
    return lines[0].count("|") >= 2 and lines[0].count("|") == lines[-1].count("|")


def _classify(block: list[str]) -> tuple[NodeKind, int]:
    first = block[0].strip()
    if len(block) >= 2 and _is_table(block):
        return ("table", 0)
    if first.startswith("#"):
        return ("heading", len(first) - len(first.lstrip("#")))
    if len(block) >= 2 and all(_LIST_ITEM.match(ln) for ln in block):
        return ("list", 0)
    return ("paragraph", 0)


def _to_nodes(text: str) -> list[CanonicalNode]:
    """结构识别第①级（规则档）：按空行分块，围栏代码/表格/标题/列表按形状判型，其余为段落。

    代码围栏内的空行不分块——围栏是结构边界，按空行切会把一个函数拆成两段（=违反 D-2「code 函数级不跨切」）。
    第②级（版面/分类小模型；Word 直读 docx 原生 XML 结构）与第③级（LLM few-shot）需真供给＝放行档，
    本函数只兑现零成本的规则判型，不冒充版面识别。
    """
    blocks: list[tuple[list[str], bool]] = []  # (行, 是否代码块)
    current: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                blocks.append((current, True))
                current = []
            in_code = not in_code
            continue
        if in_code:
            current.append(line)
            continue
        if line.strip():
            current.append(line)
        elif current:
            blocks.append((current, False))
            current = []
    if current:
        blocks.append((current, in_code))
    nodes: list[CanonicalNode] = []
    for i, (block, is_code) in enumerate(blocks):
        kind: NodeKind = "code" if is_code else "paragraph"
        level = 0
        if not is_code:
            kind, level = _classify(block)
        nodes.append(
            CanonicalNode(node_id=f"n{i}", kind=kind, text="\n".join(block).strip(), level=level)
        )
    return nodes
