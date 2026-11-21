"""M2 检索链单测：BM25 词法召回、RRF 融合序、置信/拒答口径、漏斗端到端。

零外部依赖（dev 档）：断言的是链路结构与口径正确性，非模型质量基线。
"""

from __future__ import annotations

from src.partitions.ingest.runtime.ask import AnswerAgent, QaState
from src.partitions.ingest.service.ask import citation, fusion, planner, policy
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.providers.bm25_dev import Bm25Index
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.llm_dev import DevEchoLlm
from src.platform.providers.orchestration_plain import PlainOrchestration
from src.platform.providers.rerank_dev import DevLexicalRerank
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Chunk, Document


def _chunk(cid: str, text: str, acl: list[str] | None = None, authority: str = "source") -> Chunk:
    return Chunk(chunk_id=cid, doc_id=cid.split("#")[0], doc_version=1, seq=0, text=text, acl_tags=acl or [], authority=authority)  # type: ignore[arg-type]


def test_bm25_recalls_relevant_and_filters_acl() -> None:
    idx = Bm25Index()
    idx.index(
        [
            _chunk("a#0", "企业知识库向量检索与重排方案"),
            _chunk("b#0", "食堂菜单与采购清单"),
            _chunk("c#0", "机密：向量检索性能基准", acl=["secret"]),
        ]
    )
    hits = idx.search("向量检索方案", topk=5, acl_tags=[])
    ids = [h.chunk_id for h in hits]
    assert "a#0" in ids
    assert "b#0" not in ids  # 无关文档不进结果
    assert "c#0" not in ids  # 越权块被 pre-filter 拦下（FR-CAP-4 明令禁 post-filter：无权块在召回阶段即不进候选，非"先检索后丢弃"）
    assert idx.search("向量检索", topk=5, acl_tags=["secret"])[0].chunk_id in {"a#0", "c#0"}


def test_rrf_promotes_docs_hit_by_both_paths() -> None:
    order = fusion.reciprocal_rank_fusion([["x", "y", "z"], ["y", "w", "x"]], k=60)
    # y 在两路都靠前 → 融合最高；x 两路都有但名次靠后次于 y
    assert order[0] == "y"
    assert set(order[:2]) == {"y", "x"}


def test_fuse_lists_dedups_and_keeps_wiki_authority() -> None:
    v = [_chunk("d#0", "正文"), _chunk("e#0", "其它")]
    b = [_chunk("d#0", "正文", authority="wiki"), _chunk("f#0", "新块")]
    fused = fusion.fuse_lists(v, b, k=60)
    ids = [c.chunk_id for c in fused]
    assert len(ids) == len(set(ids)) == 3  # 去重
    assert next(c for c in fused if c.chunk_id == "d#0").authority == "wiki"


def test_confidence_and_refusal_gate() -> None:
    chunks = [_chunk("a#0", "执行档摄取与检索说明"), _chunk("b#0", "执行档重排与引用")]
    conf = policy.answer_confidence("执行档摄取", chunks, topn_min=5)
    assert conf > 0.0
    assert policy.is_refused(conf, threshold=0.99)  # 高阈→拒
    assert not policy.is_refused(conf, threshold=0.35)
    assert policy.answer_confidence("无关问题", [], topn_min=5) == 0.0


def test_funnel_end_to_end_returns_cited_answer() -> None:
    container = build_container(Settings())
    text = "# 检索\n\n" + "\n\n".join(f"第{i}段：向量检索与 BM25 融合的执行档说明。" for i in range(30))
    container.ingest.process_document(Document(doc_id="d1", domain="hr", acl_tags=["hr"], raw_text=text))
    ans = container.app.answer("向量检索融合", user_acl=["hr"])
    assert not ans.refused
    assert ans.citations
    assert 0.35 <= ans.confidence <= 1.0
    denied = container.app.answer("向量检索融合", user_acl=["outsider"])
    assert denied.refused and denied.confidence == 0.0


def test_citation_enforce_drops_ungrounded_sentences() -> None:
    valid = {"a#0", "b#0"}
    report = citation.enforce("第一句有依据[a#0]。这句是编造的无出处结论。第二句有依据[b#0]。", valid)
    assert report.used_chunk_ids == ["a#0", "b#0"]
    assert len(report.dropped) == 1 and "编造" in report.dropped[0]
    assert "编造" not in report.text


def test_citation_rejects_unknown_source_id() -> None:
    report = citation.enforce("结论引用了不存在的出处[halluc#9]。", {"a#0"})
    assert not report.grounded  # 标记不在本轮召回集合内→不算出处


def test_app_refuses_when_all_sentences_ungrounded() -> None:
    from src.platform.providers.llm_dev import DevEchoLlm

    class UngroundedLlm:
        def complete(self, prompt: str, max_tokens: int = 512) -> str:
            return "这是一句没有任何引用标记的断言。"

    container = build_container(Settings())
    text = "# 检索\n\n" + "\n\n".join(f"第{i}段：向量检索与 BM25 融合的执行档说明。" for i in range(30))
    container.ingest.process_document(Document(doc_id="d1", domain="hr", acl_tags=["hr"], raw_text=text))
    container.app._agent._llm = UngroundedLlm()  # 注入无标记输出，验证 FR-ASK-2 全拦即拒答
    ans = container.app.answer("向量检索融合", user_acl=["hr"])
    assert ans.refused and not ans.citations
    container.app._agent._llm = DevEchoLlm()  # 复原，避免污染其它用例


def test_graph_run_while_bounded_by_stop_and_budget() -> None:
    g = PlainOrchestration()
    # 步预算先绑：即便 stop 未满足，也只跑到 max_steps 次。
    n = g.run_while([lambda s: s + 1], 0, stop=lambda s: False, max_steps=3)
    assert n == 3
    # stop 先绑：达到收敛即停，不耗尽预算。
    n = g.run_while([lambda s: s + 1], 0, stop=lambda s: s >= 2, max_steps=8)
    assert n == 2


def test_planner_expand_adds_cross_chunk_bridge_terms_only() -> None:
    chunks = [_chunk("a#0", "预算编制 附录 手册"), _chunk("b#0", "预算编制 附录 说明")]
    expanded = planner.expand_query("预算编制", chunks, max_terms=5)
    assert expanded.startswith("预算编制")
    assert "附录" in expanded  # 跨块高频桥接词被补进查询
    assert "预算编制" in expanded


def _agent(settings: Settings) -> AnswerAgent:
    return AnswerAgent(
        settings,
        DevHashEmbedding(dim=8),
        MemoryVectorStore(),
        DevLexicalRerank(),
        DevEchoLlm(),
        PlainOrchestration(),
    )


def test_multihop_loop_surfaces_new_evidence_and_bounded() -> None:
    s = Settings(agent_confidence_target=0.99, agent_max_hops=5, answer_topn_min=5)
    agent = _agent(s)
    c_a = _chunk("a#0", "预算编制 参考 附录 手册 详细说明")
    c_b = _chunk("b#0", "预算编制 附录 手册 更多规定")
    calls = {"n": 0}

    def fake_recall(query: str, acl: list[str]) -> list[Chunk]:
        calls["n"] += 1
        # 第1跳只 c_a；第2跳补到 c_b（新证据）；第3跳无新证据→停。
        return [c_a] if calls["n"] == 1 else [c_a, c_b]

    agent._recall = fake_recall  # type: ignore[method-assign]
    state = agent._gather(QaState(question="预算编制", query="预算编制"))
    assert state.hops == 2  # 第二跳补到 c_b 记一跳；第三跳无新证据不计数即停
    assert calls["n"] == 3
    assert len(state.ranked) == 2
    assert state.confidence < s.agent_confidence_target  # 未收敛→降级（answer() 会标 degraded）


def test_multihop_respects_single_hop_budget() -> None:
    s = Settings(agent_confidence_target=0.99, agent_max_hops=1)
    agent = _agent(s)
    c_a = _chunk("a#0", "预算编制 参考 附录 手册 详细说明")
    calls = {"n": 0}

    def one_recall(query: str, acl: list[str]) -> list[Chunk]:
        calls["n"] += 1
        return [c_a]

    agent._recall = one_recall  # type: ignore[method-assign]
    state = agent._gather(QaState(question="预算编制", query="预算编制"))
    assert state.hops == 1 and calls["n"] == 1  # 预算=1：不触发第二跳再检索
    assert state.confidence < s.agent_confidence_target


