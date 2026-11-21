"""M4-ay FR-CAP-2 读侧降级阶梯（④→③→②）执行半环：档位判定、上限映射、问答链真实降档与组合根信号源。

断言的是**结构不变式**（降什么、保什么、失败与否），不声称真实流量下的容量收益。
关键立场=阶梯只减功能、不加失败：档 2 也要给用户"次好答案+说明"（可呈现的 `Answer`），而非异常。
"""

from __future__ import annotations

from src.partitions.ingest.runtime.ask import AnswerAgent, QaState
from src.partitions.ingest.service.ask import ladder
from src.partitions.ingest.service.ask.funnel import FunnelLedger
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.llm_dev import DevEchoLlm
from src.platform.providers.orchestration_plain import PlainOrchestration
from src.platform.providers.rerank_dev import DevLexicalRerank
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Chunk


class CountingLlm:
    """计数包装：证明降档到底有没有发起生成（=是否真的弃掉了③层）。"""

    def __init__(self) -> None:
        self.calls = 0
        self._echo = DevEchoLlm()

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        self.calls += 1
        return self._echo.complete(prompt, max_tokens)


def _chunk(cid: str, text: str, authority: str = "source") -> Chunk:
    return Chunk(  # type: ignore[arg-type]
        chunk_id=cid, doc_id=cid.split("#")[0], doc_version=1, seq=0, text=text, authority=authority
    )


def _agent(settings: Settings, llm: object, level: object = None, ledger: FunnelLedger | None = None) -> AnswerAgent:
    agent = AnswerAgent(
        settings,
        DevHashEmbedding(dim=8),
        MemoryVectorStore(),
        DevLexicalRerank(),
        llm,  # type: ignore[arg-type]
        PlainOrchestration(),
        funnel_ledger=ledger,
        degrade_level=level,  # type: ignore[arg-type]
    )
    return agent


def _state(caps: ladder.ReadCaps) -> QaState:
    s = QaState(question="预算编制", query="预算编制")
    s.caps = caps
    return s


def _wiki_recall(chunks: list[Chunk]):
    def fake_recall(query: str, acl: list[str]) -> list[Chunk]:
        return list(chunks)

    return fake_recall


def test_ladder_level_uses_b2_soft_and_hard_boundaries() -> None:
    assert ladder.ladder_level(5, soft=6, hard=8) == 0
    assert ladder.ladder_level(6, soft=6, hard=8) == 1  # 越 soft 即弃④（与 B2 alarm 同点，不等硬断）
    assert ladder.ladder_level(7, soft=6, hard=8) == 1
    assert ladder.ladder_level(8, soft=6, hard=8) == 2  # 达 hard 弃④③
    assert ladder.ladder_level(9, soft=6, hard=8) == 2


def test_caps_for_maps_level_to_read_caps() -> None:
    normal = ladder.caps_for(0, agent_max_hops=8)
    assert (normal.level, normal.max_hops, normal.allow_generation) == (0, 8, True)
    first = ladder.caps_for(1, agent_max_hops=8)
    assert (first.level, first.max_hops, first.allow_generation) == (1, 1, True)
    assert "弃④" in first.reason
    second = ladder.caps_for(2, agent_max_hops=8)
    assert (second.level, second.max_hops, second.allow_generation) == (2, 1, False)
    assert "弃④③" in second.reason
    assert second.max_hops >= 1  # 任何档都至少保留一次检索（降到 0 跳=不再服务=变相失败）
    clamped = ladder.caps_for(7, agent_max_hops=8)  # 未知高档钳到本阶梯最高执法档，不静默当正常
    assert clamped.level == ladder.MAX_INFLIGHT_LEVEL and clamped.allow_generation is False


def test_fallback_page_picks_first_wiki_authority_chunk() -> None:
    wiki = _chunk("w#0", "薪酬制度总则", authority="wiki")
    src = _chunk("s#0", "原文条款")
    assert ladder.fallback_page([src, wiki]) is wiki
    assert ladder.fallback_page([src]) is None
    assert ladder.fallback_page([]) is None


def test_agent_at_level_two_stops_generation_and_serves_wiki_page() -> None:
    """档 2：零 LLM 调用 + ②层既有页直答 + degraded 说明 + 漏斗记 wiki（不是报错）。"""
    s = Settings(agent_confidence_target=0.99, reject_threshold=0.0)
    led = FunnelLedger()
    llm = CountingLlm()
    agent = _agent(s, llm, level=lambda: 2, ledger=led)
    agent._recall = _wiki_recall(  # type: ignore[method-assign]
        [_chunk("s#0", "薪酬制度 加班 规定 原文"), _chunk("w#0", "薪酬制度 加班 规定 wiki 总则", authority="wiki")]
    )
    ans = agent.answer("薪酬制度 加班", ["hr"])
    assert llm.calls == 0  # ③层生成被弃
    assert ans.refused is False and ans.degraded is True
    assert ans.text.startswith(ladder.DEGRADE_NOTICE) and "wiki 总则" in ans.text
    assert [c.chunk_id for c in ans.citations] == ["w#0"] and ans.citations[0].authority == "wiki"
    assert led.snapshot().counts["wiki"] == 1 and led.snapshot().effective == 1


def test_agent_at_level_two_without_wiki_page_refuses_with_notice_not_error() -> None:
    """无②层可给时=如实拒答并说明（可呈现的 Answer），仍零生成、仍计 refused 不扩命中率分母。"""
    s = Settings(agent_confidence_target=0.99, reject_threshold=0.0)
    led = FunnelLedger()
    llm = CountingLlm()
    agent = _agent(s, llm, level=lambda: 2, ledger=led)
    agent._recall = _wiki_recall([_chunk("s#0", "薪酬制度 加班 规定 原文")])  # type: ignore[method-assign]
    ans = agent.answer("薪酬制度 加班", ["hr"])
    assert llm.calls == 0
    assert ans.refused is True and ans.degraded is True and ans.text.startswith(ladder.DEGRADE_NOTICE)
    snap = led.snapshot()
    assert snap.effective == 0 and snap.refused == 1


def test_agent_at_level_one_caps_multihop_budget_only() -> None:
    s = Settings(agent_confidence_target=0.99, agent_max_hops=5, reject_threshold=0.0)
    chunks = [_chunk(f"c{i}#0", f"预算编制 附录 手册 第{i}条 详细说明") for i in range(4)]
    calls = {"n": 0}

    def growing_recall(query: str, acl: list[str]) -> list[Chunk]:
        calls["n"] += 1
        return chunks[: calls["n"]]

    agent = _agent(s, DevEchoLlm())
    agent._recall = growing_recall  # type: ignore[method-assign]
    capped = agent._gather(_state(ladder.caps_for(1, agent_max_hops=5)))
    assert calls["n"] == 1 and capped.hops == 1  # 档 1：第二跳预算被收走=弃④

    calls["n"] = 0
    normal = agent._gather(_state(ladder.caps_for(0, agent_max_hops=5)))
    assert calls["n"] > 1 and normal.hops > 1  # 正常档：多跳照常展开（反证降档确实在动旋钮）


def test_agent_without_level_source_behaves_as_before() -> None:
    """未接档位源=正常档：生成照常发起（本刀不改变默认行为）。"""
    s = Settings(reject_threshold=0.0)
    llm = CountingLlm()
    agent = _agent(s, llm)
    agent._recall = _wiki_recall([_chunk("s#0", "薪酬制度 加班 规定 原文")])  # type: ignore[method-assign]
    ans = agent.answer("薪酬制度 加班", ["hr"])
    assert agent._caps().level == 0
    assert llm.calls == 1 and ans.refused is False


def test_composition_feeds_inflight_level_to_read_path() -> None:
    """组合根把 B2 分池账本的在飞水位接成读侧档位源（打满 hard 即自动降到档 2）。"""
    container = build_container()
    agent = container.app._agent
    assert agent._caps().level == 0  # 空账本=正常档
    for _ in range(container.settings.inflight_llm_limit):
        container.inflight.acquire()
    caps = agent._caps()
    assert caps.level == 2 and caps.allow_generation is False and caps.max_hops == 1


def test_level_two_still_serves_semantic_cache_hit() -> None:
    """①层缓存命中=零成本最高优先级降级形态，阶梯不拦（同问同 ACL 在 hard 之下仍直接返回缓存件、不发起生成）。"""
    from src.partitions.ingest.service.ask import cache
    from src.platform.types import Answer, Citation

    container = build_container()
    q, acl = "薪酬制度如何规定", ["hr"]
    container.cache.put(
        cache.cache_key(q, acl),
        Answer(
            question=q,
            text="有据答案",
            citations=[Citation(chunk_id="d#0", doc_id="d", doc_version=1)],
            confidence=0.9,
        ),
    )
    for _ in range(container.settings.inflight_llm_limit):
        container.inflight.acquire()  # 打满 hard=level 2
    assert container.app._agent._caps().level == 2
    got = container.app.answer(q, user_acl=acl)
    assert got.refused is False and got.text == "有据答案"  # 缓存命中直返，非【高负载降级】
    assert not got.text.startswith(ladder.DEGRADE_NOTICE)
    snap = container.funnel.snapshot()
    assert snap.counts["cache"] == 1 and snap.effective == 1  # 归属①层、不受阶梯影响


def test_level_two_wiki_fallback_never_leaks_cross_acl() -> None:
    """降级不越权不变式：level 2 wiki 直答的候选=已通过 ACL pre-filter 的 state.ranked；
    用户无权域的 wiki 页在 pre-filter 阶段即被剔除，阶梯**绝不绕权限**给出跨域 wiki。"""
    from src.platform.types import Chunk as _C

    def _acl_chunk(cid: str, text: str, acl: list[str], authority: str) -> _C:
        return _C(  # type: ignore[arg-type]
            chunk_id=cid, doc_id=cid.split("#")[0], doc_version=1, seq=0,
            text=text, acl_tags=acl, authority=authority,
        )

    secret_wiki = _acl_chunk("sec#0", "机密薪酬总则 wiki", ["secret"], "wiki")
    hr_source = _acl_chunk("hr#0", "薪酬制度 加班 hr 原文", ["hr"], "source")

    def acl_recall(query: str, acl: list[str]) -> list[_C]:
        # 模拟 pre-filter：无权块的 chunk 根本不进候选（同 FR-CAP-4 pre-filter 明令禁 post-filter）
        return [c for c in (secret_wiki, hr_source) if set(c.acl_tags) & set(acl)]

    s = Settings(agent_confidence_target=0.99, reject_threshold=0.0)
    agent = _agent(s, DevEchoLlm(), level=lambda: 2)
    agent._recall = acl_recall  # type: ignore[method-assign]
    ans = agent.answer("薪酬制度 加班", ["hr"])
    # hr 用户经 pre-filter 只剩 hr_source、无 wiki 权威页→level 2 只能透明拒答，绝不把 secret_wiki 泄出
    assert ans.refused is True and ans.degraded is True
    assert ans.text.startswith(ladder.DEGRADE_NOTICE)
    assert "机密薪酬总则" not in ans.text  # 反证：跨域 wiki 内容未通过降级通道泄漏
    assert all(c.chunk_id != "sec#0" for c in ans.citations)


def test_enterprise_hard_50_scales_ladder_thresholds() -> None:
    """§8.8 企业档 in-flight 硬上限=50 时，分池账本按 5:2:1 定档 31/13/6、峰水位=38，
    阶梯 level 判据随之整体外扩（38→level 1、50→level 2）——单机/企业两档**同一硬编码不漂移**：
    `ladder_level(count, soft=peak, hard=budget.hard)` 由 `PoolBudget.for_hard(hard)` 从 Settings 驱动。"""
    from src.platform.breaker.pool import PoolBudget

    ent = PoolBudget.for_hard(50)
    assert (ent.own["qa"], ent.own["wiki_compile"], ent.own["evolve"]) == (31, 13, 6)
    assert (ent.peak, ent.hard) == (38, 50)
    # 阶梯判据按企业档阈值正确切换
    assert ladder.ladder_level(37, soft=ent.peak, hard=ent.hard) == 0
    assert ladder.ladder_level(38, soft=ent.peak, hard=ent.hard) == 1  # 峰水位→弃④保③
    assert ladder.ladder_level(49, soft=ent.peak, hard=ent.hard) == 1
    assert ladder.ladder_level(50, soft=ent.peak, hard=ent.hard) == 2  # 硬上限→弃④③保②
    # 组合根企业档：inflight_llm_limit=50 时池/阶梯同源，50 acquire 必达 level 2
    container = build_container(Settings(inflight_llm_limit=50))
    assert container.app._agent._caps().level == 0
    for _ in range(50):
        container.inflight.acquire()
    assert container.app._agent._caps().level == 2  # 无第二张皮、单一口径


def test_level_two_wiki_direct_answer_redacts_planted_secret() -> None:
    """真数据：wiki 页文本植入 `api_key: XXXX` 样式；level 2 直答必须经 dlp.redact 出口纵深（同源 M4-p 家族）。"""
    from src.partitions.ingest.service import dlp
    from src.partitions.ingest.service.ask.funnel import FunnelLedger

    leaked = "薪酬制度 加班 规定 wiki 总则，附录含 api_key: SUPERSECRETPATTERN"
    wiki_page = _chunk("w#0", leaked, authority="wiki")
    s = Settings(agent_confidence_target=0.99, reject_threshold=0.0)
    led = FunnelLedger()
    llm = CountingLlm()
    agent = _agent(s, llm, level=lambda: 2, ledger=led)
    agent._recall = _wiki_recall([wiki_page])  # type: ignore[method-assign]
    ans = agent.answer("薪酬制度 加班", ["hr"])
    # ①阶梯仍生效（未生成、走 wiki 直答）
    assert llm.calls == 0 and ans.refused is False and ans.degraded is True
    assert ans.text.startswith(ladder.DEGRADE_NOTICE)
    # ②出口 DLP 纵深：敏感模式已脱敏、原文不再出现在答复文本内
    assert "SUPERSECRETPATTERN" not in ans.text, "level 2 wiki 直答绕过 dlp.redact=§12 生成后门家族回归"
    # ③funnel 归属仍=wiki（DLP 只改文本、不改层级）
    assert led.snapshot().counts["wiki"] == 1
    # ④出口闸确实被触发（redact 命中≥1 且与生成出口同源 SECRET_RE）
    _, hits = dlp.redact(leaked)
    assert hits >= 1 and dlp.find_secrets(leaked)


def test_level_two_wiki_direct_answer_never_populates_cache() -> None:
    """M4-bc FR-CAP-1①：降级件绝不入缓存——档 2 wiki 直答虽接地但属降级呈现，若入缓存则下次同问以"全质量"命中冒充正常答案（降级件污染最高优先级层）。此前"降级件不入缓存"仅由档 2 分支早返回（`ask.py` cache.put 之前 return）间接保证、无直测，防未来重构把缓存写入提前或误加降级写路径。"""
    from src.partitions.ingest.service.ask import cache as cache_mod
    from src.platform.providers.cache_memory import MemoryCache

    s = Settings(agent_confidence_target=0.99, reject_threshold=0.0)
    cache = MemoryCache()
    llm = CountingLlm()
    agent = AnswerAgent(
        s,
        DevHashEmbedding(dim=8),
        MemoryVectorStore(),
        DevLexicalRerank(),
        llm,  # type: ignore[arg-type]
        PlainOrchestration(),
        funnel_ledger=FunnelLedger(),
        degrade_level=lambda: 2,  # type: ignore[arg-type]
        cache_store=cache,  # type: ignore[arg-type]
    )
    agent._recall = _wiki_recall([_chunk("w#0", "薪酬制度 加班 规定 wiki 总则", authority="wiki")])  # type: ignore[method-assign]
    q = "薪酬制度 加班"
    ans = agent.answer(q, ["hr"])
    # ①阶梯仍生效：零生成、走 wiki 直答、标记降级
    assert llm.calls == 0 and ans.refused is False and ans.degraded is True
    assert ans.text.startswith(ladder.DEGRADE_NOTICE)
    # ②降级件不入缓存（①层永不被降档答案冒充全质量命中）
    assert cache.get(cache_mod.cache_key(q, ["hr"])) is None
