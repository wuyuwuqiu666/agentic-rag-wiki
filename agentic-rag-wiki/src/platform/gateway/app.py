"""组合根产出的应用外壳：只读问答入口（薄壳，委派 A 区 runtime 的 AnswerAgent）。

架构自查 F1/F2：检索编排（漏斗/多跳/引用/置信/熔断采集）归 A 区 `partitions/ingest/runtime/ask.py`，
经 `GraphPort` 声明；gateway 只做装配与委派（与摄取链经 `IngestPipeline` 对称）。换 provider 不影响本链（G5）。
"""

from __future__ import annotations

from collections.abc import Callable

from src.partitions.ingest.runtime.ask import AnswerAgent
from src.partitions.ingest.service.ask.funnel import FunnelLedger
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
from src.platform.providers.orchestration_plain import PlainOrchestration
from src.platform.types import Answer


class App:
    def __init__(
        self,
        settings: Settings,
        embedding: EmbeddingPort,
        vectorstore: VectorStorePort,
        rerank: RerankPort,
        llm: LlmPort,
        guard: GuardPort | None = None,
        audit: AuditPort | None = None,
        lexical: LexicalPort | None = None,
        inflight: InflightPort | None = None,
        graph: GraphPort | None = None,
        cache_store: CachePort | None = None,
        funnel_ledger: FunnelLedger | None = None,
        degrade_level: Callable[[], int] | None = None,
    ) -> None:
        self._agent = AnswerAgent(
            settings,
            embedding,
            vectorstore,
            rerank,
            llm,
            graph or PlainOrchestration(),
            guard=guard,
            audit=audit,
            lexical=lexical,
            inflight=inflight,
            cache_store=cache_store,
            funnel_ledger=funnel_ledger,
            degrade_level=degrade_level,
        )

    def answer(self, question: str, user_acl: list[str] | None = None, trace_id: str | None = None) -> Answer:
        return self._agent.answer(question, user_acl, trace_id)
