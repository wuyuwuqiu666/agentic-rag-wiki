"""FR-ING-7 邻居回取消费端测试：命中块按 prev/next 从候选池补邻块重建上下文（重叠=0 的替代机制）。

此前 chunker 早已落 prev/next 链却从不被检索侧消费=写而不读的孤立缝（trace_id 同族）。
纯函数用例锁补全/预算/ACL/去重/不臆造口径；端到端用例证明邻块确被并入上下文并可被引用（消费坐实）。
"""

from __future__ import annotations

from src.partitions.ingest.runtime.ask import AnswerAgent, QaState
from src.partitions.ingest.service.ask import neighbor
from src.platform.config import Settings
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.orchestration_plain import PlainOrchestration
from src.platform.providers.rerank_dev import DevLexicalRerank
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Chunk


def _c(cid: str, text: str, *, prev: str | None = None, nxt: str | None = None, tok: int = 100) -> Chunk:
    return Chunk(
        chunk_id=cid, doc_id=cid.split("#")[0], doc_version=1, seq=0,
        text=text, prev=prev, next=nxt, token_count=tok,
    )


def test_pulls_prev_then_next_and_keeps_hits_first() -> None:
    p0, p1, p2 = _c("d#0", "前", nxt="d#1"), _c("d#1", "中", prev="d#0", nxt="d#2"), _c("d#2", "后", prev="d#1")
    pool = [p0, p1, p2]
    ctx = neighbor.expand_neighbors([p1], pool, max_added_tokens=1000)
    assert [c.chunk_id for c in ctx] == ["d#1", "d#0", "d#2"]  # 命中在前，先补前驱再补后继


def test_budget_skips_oversized_neighbor() -> None:
    hit = _c("d#1", "中", prev="d#0", nxt="d#2")
    big_prev = _c("d#0", "很大前块", tok=5000)
    small_next = _c("d#2", "小后块", tok=100)
    pool = [big_prev, hit, small_next]
    ctx = neighbor.expand_neighbors([hit], pool, max_added_tokens=1000)
    ids = [c.chunk_id for c in ctx]
    assert "d#0" not in ids  # 越预算的前驱跳过
    assert "d#2" in ids  # 后续更小邻块仍补入（非"一越预算即全断"）


def test_neighbor_absent_in_pool_is_not_fabricated() -> None:
    hit = _c("d#1", "中", prev="d#0", nxt="d#9")  # d#9 未召回（跨域未授权/未命中）
    pool = [hit]  # 池里没有 d#0/d#9
    ctx = neighbor.expand_neighbors([hit], pool, max_added_tokens=1000)
    assert [c.chunk_id for c in ctx] == ["d#1"]  # 池外邻块不臆造、不旁路取


def test_dedup_shared_neighbor_and_existing_hit() -> None:
    a = _c("d#1", "A", nxt="d#2")
    b = _c("d#2", "B", prev="d#1")  # b 既是命中又是 a 的后继
    ctx = neighbor.expand_neighbors([a, b], [a, b], max_added_tokens=1000)
    ids = [c.chunk_id for c in ctx]
    assert ids == ["d#1", "d#2"]  # 命中已含 d#2，a 的 next 去重不重复补


def _stub_agent(settings: Settings, *, ranked: list[Chunk], evidence: list[Chunk]) -> AnswerAgent:
    agent = AnswerAgent(
        settings, DevHashEmbedding(dim=8), MemoryVectorStore(),
        DevLexicalRerank(), _CiteNeighbor(), PlainOrchestration(),
    )
    st = QaState(question="q", acl=[], query="q", confidence=0.8, hops=1, visible=True)
    st.ranked = ranked
    st.evidence = evidence
    agent._gather = lambda _s: st  # type: ignore[method-assign]
    return agent


class _CiteNeighbor:
    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        return "结论依据邻块上下文[d#2]。"  # 引用一个只可能来自邻居回取的块


def test_end_to_end_neighbor_is_consumed_and_citable() -> None:
    s = Settings(neighbor_refetch=True, neighbor_refetch_tokens=2000)
    hit = _c("d#1", "命中块", nxt="d#2", tok=100)
    neighbor_chunk = _c("d#2", "邻块正文", prev="d#1", tok=100)
    agent = _stub_agent(s, ranked=[hit], evidence=[hit, neighbor_chunk])
    ans = agent._answer("q", [])
    assert not ans.refused
    assert {c.chunk_id for c in ans.citations} == {"d#2"}  # 邻块经回取进入可引用证据集=prev/next 确被消费


def test_end_to_end_off_disables_refetch() -> None:
    s = Settings(neighbor_refetch=False)
    hit = _c("d#1", "命中块", nxt="d#2", tok=100)
    neighbor_chunk = _c("d#2", "邻块正文", prev="d#1", tok=100)
    agent = _stub_agent(s, ranked=[hit], evidence=[hit, neighbor_chunk])
    ans = agent._answer("q", [])
    # 关闭回取→上下文仅 [d#1]，LLM 引用的 d#2 不在证据集→无据句全拦→拒答（反证 d#2 入上下文确系邻居回取所致）。
    assert ans.refused and not ans.citations
