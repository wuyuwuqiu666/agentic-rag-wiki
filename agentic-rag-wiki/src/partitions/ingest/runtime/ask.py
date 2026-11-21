"""A 区问答运行时（FR-ASK 全链编排，经 GraphPort 声明）：漏斗→多跳回路→引用校验→出口。

层职责归位（架构自查 F1/F2）：检索编排是 A 区 runtime 的活，gateway 只作薄壳委派（与摄取链 IngestPipeline 对称）。
- 漏斗（FR-ASK-5）：pre-filter(ACL)→向量 topk + BM25 topk→RRF k60→rerank≤50→top-n 5–8→LLM。
- 邻居回取（FR-ING-7）：top-n 命中后按 `prev`/`next` 从已过 ACL 的候选池补邻块重建生成上下文（overlap=0 替代），`service/ask/neighbor`。
- 读侧四级漏斗（FR-CAP-1）：①语义缓存命中（`service/ask/cache` 准入=键含 ACL 域指纹、无引用不缓存）→②/③/④ 下述检索与多跳。命中率数字仅企业档生效，层本身=代码路径+单测断言；每层归属与拒答计入 `service/ask/funnel` 稳态口径账本（分子=层命中、分母=有效请求，拒答不入分母），经 `gateway.Admin.funnel_stats` 读回。
- 多跳（FR-ASK-1）：`GraphPort.run_while` 跑条件边回路，低置信→扩写再检索，≤agent_max_hops，未收敛标 degraded。
- 引用（FR-ASK-2）/置信（FR-ASK-3）：纯判定下沉 service/ask，本模块只调度。
- 出口 DLP（§12 生成后门 / §8.5 B4 出口）：答案文本经 `service/dlp` 脱敏后呈现，命中即发 B4 信号（v1 shadow 不阻断）且不入缓存。
- 熔断采集（§8 框架边界）：B2 in-flight、B3 fail-loud 在此发生，判定交 GuardPort（不委托编排框架回调）。
一切依赖由组合根注入（端口），本模块零 SDK、零跨区 import（check_imports 执法）。
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from src.partitions.ingest.service import dlp
from src.partitions.ingest.service.ask import (
    cache,
    citation,
    cost,
    funnel,
    fusion,
    ladder,
    neighbor,
    planner,
    policy,
)
from src.platform.config import Settings
from src.platform.providers import (
    AuditPort,
    CachePort,
    EmbeddingPort,
    GraphPort,
    GuardPort,
    InflightPort,
    LexicalPort,
    LlmPort,
    RerankPort,
    VectorStorePort,
)
from src.platform.providers.errors import ProviderError
from src.platform.types import Answer, AuditEvent, Chunk, Citation

# §15 E4「结构化日志带 trace_id 贯通会话」：一次问答会话 = 一个 trace_id，贯穿该会话内所有审计事件
# 与最终 Answer。用 ContextVar 而非实例属性——会话态不得挂在共享实例上（并发/多跳子调用会串号）；
# ContextVar 天然按调用上下文隔离，dev PlainOrchestration 同步执行可直接传播，换线程化 GraphPort 时按 context 复制仍安全。
_trace_var: contextvars.ContextVar[str] = contextvars.ContextVar("rag_trace_id", default="")


@dataclass
class QaState:
    """问答回路运行态（多跳每迭代推进；换 langgraph 时映射为 StateGraph checkpoint）。"""

    question: str
    acl: list[str] = field(default_factory=list)
    query: str = ""  # 当前跳检索词（首跳=question，之后扩写）
    evidence: list[Chunk] = field(default_factory=list)
    ranked: list[Chunk] = field(default_factory=list)
    confidence: float = 0.0
    hops: int = 0
    visible: bool = True
    new_added: bool = True
    # FR-CAP-2 本请求的读侧降档上限：入口取一次、随状态走（会话态不挂共享实例，同 trace_id 口径）。
    caps: ladder.ReadCaps | None = None


class AnswerAgent:
    def __init__(
        self,
        settings: Settings,
        embedding: EmbeddingPort,
        vectorstore: VectorStorePort,
        rerank: RerankPort,
        llm: LlmPort,
        graph: GraphPort,
        guard: GuardPort | None = None,
        audit: AuditPort | None = None,
        lexical: LexicalPort | None = None,
        inflight: InflightPort | None = None,
        cache_store: CachePort | None = None,
        funnel_ledger: funnel.FunnelLedger | None = None,
        degrade_level: Callable[[], int] | None = None,
    ) -> None:
        self._s = settings
        self._emb = embedding
        self._vs = vectorstore
        self._rerank = rerank
        self._llm = llm
        self._graph = graph
        self._guard = guard
        self._audit = audit
        self._lexical = lexical
        self._inflight = inflight
        self._cache = cache_store
        self._funnel = funnel_ledger
        self._degrade_level = degrade_level

    def answer(self, question: str, user_acl: list[str] | None = None, trace_id: str | None = None) -> Answer:
        """会话入口：生成/复用 trace_id → 挂到 ContextVar → 委派编排 → 出口给 Answer 盖章。

        同一会话内 `_emit` 的每条审计事件与最终 Answer 共享此 trace_id（§15 E4 贯通）；缓存命中件同样
        以本次会话 trace 呈现（响应的 trace 用于日志关联，不代表内容原始出处）。
        """
        tid = trace_id or uuid.uuid4().hex
        token = _trace_var.set(tid)
        try:
            ans = self._answer(question, user_acl or [])
            return ans.model_copy(update={"trace_id": tid})
        finally:
            _trace_var.reset(token)

    def _answer(self, question: str, acl: list[str]) -> Answer:
        # FR-CAP-1 漏斗①：语义缓存命中（键含 ACL 域指纹，跨域天然不同键→绝不越权复用）。
        key = cache.cache_key(question, acl)
        if self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                self._tally("cache")  # FR-CAP-1 漏斗①：命中即归属缓存层（短路，不经检索与 LLM）
                return hit
        caps = self._caps()  # FR-CAP-2 读侧降档：入口取一次档（无档位源=正常档，行为与接阶梯前一致），随 QaState 下传
        state = QaState(question=question, acl=acl, query=question)
        state.caps = caps
        state = self._gather(state)
        if not state.visible:
            self._emit(AuditEvent(kind="acl_denied", partition="ingest", detail=f"无可见命中，拒答：{question[:40]}"))
            self._tally_refused()
            return Answer(question=question, refused=True, confidence=0.0, hops=state.hops)
        if policy.is_refused(state.confidence, self._s.reject_threshold):
            self._tally_refused()
            return Answer(
                question=question, refused=True, confidence=state.confidence, hops=state.hops,
                degraded=state.confidence < self._s.agent_confidence_target,
            )
        # FR-CAP-2 档 2：弃④③——不发起生成，改由②层已编译 wiki 权威页直答（零生成零外呼、降级件不入缓存）。
        if not caps.allow_generation:
            page = ladder.fallback_page(state.ranked)
            if page is None:
                self._tally_refused()
                self._emit(
                    AuditEvent(
                        kind="breaker_trip", partition="ingest",
                        detail=f"{caps.reason}；无可用 wiki 权威页→如实拒答并说明（可呈现的说明，不是报错）",
                        extra={"breaker": "B2", "action": "alarm", "degrade_level": caps.level},
                    )
                )
                return Answer(
                    question=question, refused=True, confidence=state.confidence, hops=state.hops, degraded=True,
                    text=f"{ladder.DEGRADE_NOTICE} 高负载且暂无可直答的 wiki 权威页，本次未生成答案",
                )
            self._tally("wiki")
            # §12 生成后门 / B4 出口纵深：wiki 页文本经入口 DLP 已过；level 2 直答属"绕生成出口"路径，
            # 复走 dlp.redact 保证与生成出口同源、防未来 wiki 合成侧规则漂移=同族 M4-p/M4-ak 单一口径。
            clean_page, page_dlp_hits = dlp.redact(page.text)
            if page_dlp_hits:
                self._emit(
                    AuditEvent(
                        kind="quarantine", partition="ingest",
                        detail=f"档 2 wiki 直答出口 DLP 命中 {page_dlp_hits} 处→已脱敏（§12 生成后门 / B4 出口纵深）",
                        extra=dlp.ruleset_tag(),
                    )
                )
                if self._guard is not None:
                    self._guard.check("injection_score", 1.0, "B4", partition="ingest")
            self._emit(
                AuditEvent(
                    kind="breaker_trip", partition="ingest",
                    detail=f"{caps.reason}；改由②层 wiki 页直答 {page.chunk_id}",
                    extra={"breaker": "B2", "action": "alarm", "degrade_level": caps.level},
                )
            )
            return Answer(
                question=question, text=f"{ladder.DEGRADE_NOTICE} {clean_page}",
                citations=[
                    Citation(
                        chunk_id=page.chunk_id, doc_id=page.doc_id, doc_version=page.doc_version,
                        authority=page.authority, breadcrumb=page.breadcrumb,
                    )
                ],
                confidence=state.confidence, hops=state.hops, degraded=True,
            )
        try:
            # FR-ING-7 邻居回取：命中 top-n 按 prev/next 从已过 ACL 的候选池补邻块重建上下文（overlap=0 的替代机制）；
            # 补入上下文既喂 LLM 亦作可引用证据集（邻块系真实同源块）。置信/排序口径仍锚 state.ranked 不变。
            context = (
                neighbor.expand_neighbors(
                    state.ranked, state.evidence, max_added_tokens=self._s.neighbor_refetch_tokens
                )
                if self._s.neighbor_refetch
                else state.ranked
            )
            raw, call_cost = self._generate(question, context)
        except ProviderError as exc:
            self._fail_loud("llm", exc)
            raise
        report = citation.enforce(raw, {c.chunk_id for c in context})  # FR-ASK-2：无据句拦截
        if not report.grounded:
            self._emit(AuditEvent(kind="acl_denied", partition="ingest", detail=f"引用校验全拦，拒答：{question[:40]}"))
            self._tally_refused()
            return Answer(question=question, refused=True, confidence=0.0, hops=state.hops)
        by_id = {c.chunk_id: c for c in context}
        cites = [
            Citation(
                chunk_id=cid, doc_id=by_id[cid].doc_id, doc_version=by_id[cid].doc_version,
                authority=by_id[cid].authority, breadcrumb=by_id[cid].breadcrumb,
            )
            for cid in report.used_chunk_ids
        ]
        degraded = state.confidence < self._s.agent_confidence_target
        est_cost = call_cost.est_money(
            per_1k_in=self._s.llm_price_per_1k_in, per_1k_out=self._s.llm_price_per_1k_out
        )
        self._emit(
            AuditEvent(
                kind="external_call", partition="ingest",
                detail=(
                    f"llm egress hops={state.hops} cites={len(cites)} dropped={len(report.dropped)} "
                    f"degraded={degraded} tok_in={call_cost.prompt_tokens} tok_out={call_cost.completion_tokens} "
                    f"total={call_cost.total_tokens} est_cost={est_cost:.6f}"
                ),
                extra={
                    "prompt_tokens": call_cost.prompt_tokens,
                    "completion_tokens": call_cost.completion_tokens,
                    "total_tokens": call_cost.total_tokens,
                    "est_cost": est_cost,
                },
            )
        )
        # §12 生成后门 / §8.5 B4 出口闸：答案出口 DLP 脱敏（入口/出口同源 `service.dlp`）。
        # v1 B4=shadow→只脱敏呈现+发信号不阻断会话（拒答由传感硬断档启用）；脱敏件不得入缓存（防污染+纵深）。
        clean_text, dlp_hits = dlp.redact(report.text)
        if dlp_hits:
            self._emit(
                AuditEvent(
                    kind="quarantine", partition="ingest",
                    detail=f"答案出口 DLP 命中 {dlp_hits} 处→已脱敏、不入缓存（§12 生成后门 / B4 出口）",
                    extra=dlp.ruleset_tag(),
                )
            )
            if self._guard is not None:
                self._guard.check("injection_score", 1.0, "B4", partition="ingest")
        ans = Answer(
            question=question, text=clean_text, citations=cites,
            confidence=state.confidence, hops=state.hops, degraded=degraded,
        )
        # FR-CAP-1 漏斗②③④ 归属：首引出处为 wiki 权威页→②，否则按跳数分③/④（①已在入口短路处计）。
        self._tally(funnel.classify_layer(leading_authority=cites[0].authority if cites else "", hops=state.hops))
        # 漏斗①出口受准入门：接地（有引用）+ 同 ACL 域 + 未拒答 + 未触发出口 DLP 方可入缓存（§12 生成后门硬条款）。
        if self._cache is not None and not dlp_hits and cache.admissible(ans, acl):
            self._cache.put(key, ans)
        return ans

    def _caps(self) -> ladder.ReadCaps:
        """取本请求的读侧降档上限（档位源未接=正常档）。"""
        level = self._degrade_level() if self._degrade_level is not None else 0
        return ladder.caps_for(level, agent_max_hops=self._s.agent_max_hops)

    def _gather(self, state: QaState) -> QaState:
        """多跳回路：经 GraphPort 条件边跑 recall→rank→advance，低置信再检索至收敛或耗尽预算。

        跳数预算取自 `state.caps`（FR-CAP-2 档 1/2 收到 1=弃④）；未带档=当前档（正常行为主）。
        """
        budget = state.caps if state.caps is not None else self._caps()
        return cast(
            "QaState",
            self._graph.run_while(
                [self._node_recall, self._node_rank, self._node_advance],
                state,
                stop=self._stop,
                max_steps=budget.max_hops,
            ),
        )

    def _node_recall(self, state: object) -> object:
        s = cast("QaState", state)
        hits = self._recall(s.query, s.acl)
        if s.hops == 0:
            s.visible = bool(hits)  # 首跳即空=ACL 过滤后无可见块
        seen = {c.chunk_id for c in s.evidence}
        added = [c for c in hits if c.chunk_id not in seen]
        s.evidence.extend(added)
        s.new_added = bool(added)
        if added or s.hops == 0:
            s.hops += 1
        return s

    def _node_rank(self, state: object) -> object:
        s = cast("QaState", state)
        s.ranked = self._rank(s.question, s.evidence)
        s.confidence = policy.answer_confidence(s.question, s.ranked, topn_min=self._s.answer_topn_min)
        return s

    def _node_advance(self, state: object) -> object:
        s = cast("QaState", state)
        s.query = planner.expand_query(s.question, s.ranked)  # 为下一跳扩写桥接词
        return s

    def _stop(self, state: object) -> bool:
        s = cast("QaState", state)
        return s.confidence >= self._s.agent_confidence_target or not s.new_added

    def _rank(self, question: str, evidence: list[Chunk]) -> list[Chunk]:
        candidates = evidence[: self._s.rerank_candidates]
        top = self._rerank.rerank(question, candidates, self._s.rerank_candidates)[: self._s.answer_topn]
        return fusion.prefer_wiki(top)  # FR-WIKI-5：同层内 wiki 权威页优先于原文

    def _recall(self, question: str, acl: list[str]) -> list[Chunk]:
        """向量路 + BM25 路双召回，RRF 融合（lexical 缺省则退化为单向量路）。"""
        try:
            query_vec = self._emb.embed([question])[0]
        except ProviderError as exc:
            self._fail_loud("embedding", exc)
            raise
        vector_hits = self._vs.search(query_vec, self._s.retrieval_vector_topk, acl)
        bm25_hits = self._lexical.search(question, self._s.retrieval_bm25_topk, acl) if self._lexical is not None else []
        return fusion.fuse_lists(vector_hits, bm25_hits, k=self._s.rrf_k)

    def _generate(self, question: str, ranked: list[Chunk]) -> tuple[str, cost.CallCost]:
        """经在飞预算槽发起 LLM 出口调用（B2 记账：越 soft 阈→软告警，不阻断），并逐次核算 token 成本（E4）。"""
        prompt = _prompt(question, ranked)
        if self._inflight is None:
            raw = self._llm.complete(prompt)
        else:
            with self._inflight.slot():
                raw = self._llm.complete(prompt)
        return raw, cost.account(prompt, raw)

    def _fail_loud(self, provider: str, exc: Exception) -> None:
        self._emit(AuditEvent(kind="provider_fail_loud", partition="ingest", detail=f"{provider} 失败：{exc}"))
        if self._guard is not None:
            self._guard.check("provider_error_rate", 1.0, "B3", partition="ingest")

    def _tally(self, layer: funnel.Layer) -> None:
        """漏斗层归属记账（账本未注入=不观测，问答链行为不变）。"""
        if self._funnel is not None:
            self._funnel.record(layer)

    def _tally_refused(self) -> None:
        """拒答不入命中率分母（口径见 `service/ask/funnel` 模块头）。"""
        if self._funnel is not None:
            self._funnel.record_refused()

    def _emit(self, event: AuditEvent) -> None:
        if self._audit is None:
            return
        tid = _trace_var.get()
        if tid and not event.trace_id:
            event = event.model_copy(update={"trace_id": tid})
        self._audit.emit(event)


def _prompt(question: str, chunks: list[Chunk]) -> str:
    ctx = "\n".join(f"[{c.chunk_id}] {c.text}" for c in chunks)
    return f"问题：{question}\n依据（每行以 [chunk_id] 开头，作答时每句末尾须标注所引用出处）：\n{ctx}"
