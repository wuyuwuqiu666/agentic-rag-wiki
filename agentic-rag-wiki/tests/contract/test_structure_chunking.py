"""FR-ING-7 结构切块（规则档＝第①级，M4-cg/ADR-010）：node 级决议 → 块型各按 D-2 档位重放。

覆盖的是一条**结构**断言，不是模型质量：同一份混合语料里 text 聚合、table 原子（超 F5 退化阈切「表头+行组」
并回填 parent_chunk_id）、code 按函数/类边界不跨切。第②③级（版面/分类小模型、LLM few-shot）需真实模型或
云出口供给＝实机放行项，本文件用规则判据的合成语料，绝不据此声称版面识别或四类语料抽检已达标。
"""

from __future__ import annotations

from pathlib import Path

from src.partitions.ingest.service import canonicalize, chunker
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.types import CanonicalDocument, ChunkDecision, Document, NodeDecision

_TABLE = "| 部门 | 预算 |\n| --- | --- |\n| 财务 | 120 |\n| 法务 | 80 |"
_CODE = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2"
_PROSE = "第一段说明文字。\n\n第二段说明文字。"


def _canonical(tmp_path: Path, text: str, doc_id: str = "d1") -> CanonicalDocument:
    doc = Document(doc_id=doc_id, version=1, domain="hr", acl_tags=["hr"], raw_text=text)
    canonical, _ = canonicalize.canonicalize(doc, FileSystemObjectStore(str(tmp_path / doc_id)))
    return canonical


# —— 结构识别第①级：canonical 节点形状 ——
def test_rule_tier_classifies_table_and_code_nodes(tmp_path: Path) -> None:
    canonical = _canonical(tmp_path, f"# 标题\n\n{_PROSE}\n\n{_TABLE}\n\n```python\n{_CODE}\n```")
    kinds = [n.kind for n in canonical.flatten()]
    assert "heading" in kinds and "paragraph" in kinds
    assert "table" in kinds and "code" in kinds


def test_code_fence_blank_line_stays_one_node(tmp_path: Path) -> None:
    """围栏内空行是代码排版不是分块边界——按空行切会把一个函数拆成两段（违 D-2「code 不跨切」）。"""
    canonical = _canonical(tmp_path, f"```python\n{_CODE}\n```")
    code_nodes = [n for n in canonical.flatten() if n.kind == "code"]
    assert len(code_nodes) == 1
    assert "return 2" in code_nodes[0].text


def test_prose_with_single_pipe_is_not_a_table(tmp_path: Path) -> None:
    """宁可少判：散文里偶然一个 | 不判成表格（误判＝把正文当原子结构块，检索不可分）。"""
    canonical = _canonical(tmp_path, "条款 A | 条款 B 的普通说明文字，只有一处竖线。")
    assert all(n.kind != "table" for n in canonical.flatten())


# —— node 级决议：一份文档三种块型并存 ——
def test_plan_emits_per_segment_chunk_types(tmp_path: Path) -> None:
    # 无围栏的裸代码不判 code（第①级只认结构标记，识别裸代码是第②③级的活）
    canonical = _canonical(tmp_path, f"{_PROSE}\n\n{_TABLE}\n\n```python\n{_CODE}\n```")
    decision = chunker.plan(canonical)
    types = [d.chunk_type for d in decision.decisions]
    assert "table" in types and "code" in types and "text" in types
    assert all(d.decider == "rule" for d in decision.decisions)  # 第①级判级，不冒充模型判型
    assert all(d.node_ids for d in decision.decisions)  # 空段决议=幻影记录，重放读不到正文


def test_decision_is_deterministic_replay(tmp_path: Path) -> None:
    canonical = _canonical(tmp_path, f"{_PROSE}\n\n{_TABLE}", doc_id="det")
    a = chunker.apply(canonical, chunker.plan(canonical))
    b = chunker.apply(canonical, chunker.plan(canonical))
    assert [(c.chunk_id, c.chunk_type, c.text) for c in a] == [(c.chunk_id, c.chunk_type, c.text) for c in b]


def test_table_never_merges_into_prose_chunk(tmp_path: Path) -> None:
    """表格段与散文段分属不同决议：跨型并入＝破坏「版面原样」的原子性。"""
    canonical = _canonical(tmp_path, f"{_PROSE}\n\n{_TABLE}")
    chunks = chunker.merge_short(chunker.apply(canonical, chunker.plan(canonical)))
    tables = [c for c in chunks if c.chunk_type == "table"]
    assert tables and all("部门" in c.text for c in tables)
    assert all(c.chunk_type != "text" or "预算" not in c.text for c in chunks)


# —— D-2：text 档位带内 + 超上界句内切 ——
def test_text_blocks_land_in_d2_band(tmp_path: Path) -> None:
    body = "\n\n".join(f"第{i}段 执行档切块说明文字，长度足以让段落聚合到档位带内。" for i in range(80))
    canonical = _canonical(tmp_path, body)
    chunks = chunker.apply(canonical, chunker.plan(canonical))
    assert len(chunks) >= 2  # 聚合不是整篇塞一块
    assert all(c.token_count <= chunker._TEXT_MAX_TOKEN for c in chunks)


def test_oversized_single_node_splits_at_sentence_end(tmp_path: Path) -> None:
    """无空行的超长单节点：硬边界用尽后按句末软边界内切，块不越 text 档上界。"""
    canonical = _canonical(tmp_path, "句子内容。" * 1200)
    chunks = chunker.apply(canonical, chunker.plan(canonical))
    assert len(chunks) >= 2
    assert all(c.token_count <= chunker._TEXT_MAX_TOKEN for c in chunks)
    assert all(c.text.endswith("。") for c in chunks)  # 切口落在句末，不切半句


# —— F5：表格退化=表头+行组，且每组重复表头 ——
def test_long_table_degrades_to_row_groups_repeating_header(tmp_path: Path) -> None:
    rows = "\n".join(f"| 行{i} | 说明内容填充填充填充 |" for i in range(400))
    canonical = _canonical(tmp_path, f"| 编号 | 说明 |\n| --- | --- |\n{rows}")
    chunks = chunker.apply(canonical, chunker.plan(canonical))
    assert len(chunks) > 1
    assert all(c.chunk_type == "table" for c in chunks)
    assert all(c.text.startswith("| 编号 | 说明 |") for c in chunks)  # 每组重复表头
    assert all(c.token_count <= chunker._TABLE_GROUP_TOKEN for c in chunks)


def test_row_groups_link_to_first_group_parent_after_renumber(tmp_path: Path) -> None:
    """parent_chunk_id 的首个真实消费者：行组父子链必须在 merge_short 重编号之后回填。"""
    rows = "\n".join(f"| 行{i} | 说明内容填充填充填充 |" for i in range(400))
    canonical = _canonical(tmp_path, f"| 编号 | 说明 |\n| --- | --- |\n{rows}")
    chunks = chunker.link_table_parents(
        chunker.merge_short(chunker.apply(canonical, chunker.plan(canonical)))
    )
    kids = [c for c in chunks if c.parent_chunk_id]
    assert kids, "退化行组必须有子块"
    ids = {c.chunk_id for c in chunks}
    assert all(c.parent_chunk_id in ids for c in kids)  # 不得指向重编号后已不存在的 id
    assert chunks[0].parent_chunk_id is None  # 首行组=父
    assert not any("group" in c.extra or "group_index" in c.extra for c in chunks)  # 临时标记不外泄


def test_atomic_table_has_no_parent_and_no_group_marker(tmp_path: Path) -> None:
    canonical = _canonical(tmp_path, _TABLE)
    chunks = chunker.link_table_parents(
        chunker.merge_short(chunker.apply(canonical, chunker.plan(canonical)))
    )
    tables = [c for c in chunks if c.chunk_type == "table"]
    assert len(tables) == 1 and tables[0].parent_chunk_id is None
    assert "group" not in tables[0].extra


# —— D-2：code 函数/类为最小单元不跨切 ——
def test_code_splits_per_function_not_across(tmp_path: Path) -> None:
    canonical = _canonical(tmp_path, f"```python\n{_CODE}\n```")
    chunks = chunker.apply(canonical, chunker.plan(canonical))
    codes = [c for c in chunks if c.chunk_type == "code"]
    assert len(codes) == 2
    assert [c.text.splitlines()[0] for c in codes] == ["def alpha():", "def beta():"]
    assert all(("def " in c.text and c.text.count("def ") == 1) or "def " not in c.text for c in codes)


# —— 决议契约形状（E3 基线不覆盖决议记录＝harness/* 禁改区，故在 tests 侧立形状锁）——
def test_decision_contract_shape_is_node_level() -> None:
    assert set(ChunkDecision.model_fields) == {"profile", "decisions", "doc_token_count"}
    assert set(NodeDecision.model_fields) == {"node_ids", "chunk_type", "confidence", "decider"}


def test_replay_ignores_segments_naming_unknown_nodes(tmp_path: Path) -> None:
    """决议指向不存在的节点=重放输入不一致，须由对账侧暴露；切块侧不臆造正文也不崩。"""
    canonical = _canonical(tmp_path, _PROSE)
    bad = ChunkDecision(
        profile="rule_structure_v1",
        decisions=[NodeDecision(node_ids=["nope"], chunk_type="text")],
        doc_token_count=canonical.token_count,
    )
    assert chunker.apply(canonical, bad) == []
