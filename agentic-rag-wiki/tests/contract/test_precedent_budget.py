"""D-3 判例预算（FR-ING-7 ③级 LLM 兜底 × B2）离线判定内核契约测试。

覆盖：10% 上限算术与边界、逐次记账、越限降级为规则默认 profile + 人审标记、判例耗尽后不再消耗（防兜底
反噬）、降级件仍可确定性重放（D-1），以及管线端到端（决议归档=降级件 + B2 软告警审计）。
真实第③级 LLM decider 与真实 tokenizer usage=实机放行项，此处一律用合成 decider，不冒充真模型判定。
"""

from __future__ import annotations

from pathlib import Path

from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.partitions.ingest.service import budget, canonicalize, chunker
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import AuditEvent, CanonicalDocument, ChunkDecision, Document, NodeDecision

_DOC_TOKENS = 1000  # raw_text 4000 字 → canonical.token_count=len//4=1000 → 判例上限 100


def _store(tmp_path: Path) -> FileSystemObjectStore:
    return FileSystemObjectStore(str(tmp_path / "obj"))


def _canonical(store: FileSystemObjectStore, doc_id: str = "doc1") -> CanonicalDocument:
    doc = Document(doc_id=doc_id, version=1, domain="hr", acl_tags=["hr"], raw_text="字" * (_DOC_TOKENS * 4))
    canonical, _ = canonicalize.canonicalize(doc, store)
    assert canonical.token_count == _DOC_TOKENS
    return canonical


def _candidate(canonical: CanonicalDocument) -> ChunkDecision:
    """合成"第③级 LLM 兜底"候选决议（真实 decider=放行项）。"""
    node_ids = [n.node_id for n in canonical.flatten()]
    return ChunkDecision(
        profile="llm_semantic_v1",
        decisions=[NodeDecision(node_ids=node_ids, chunk_type="text", confidence=0.8, decider="llm")],
        doc_token_count=canonical.token_count,
    )


def _deciders(decision: ChunkDecision) -> set[str]:
    """node 级决议（ADR-010）的判级读法：降级/透传都是整份口径，故取逐段判级集合。"""
    return {d.decider for d in decision.decisions}


def test_cap_is_ten_percent_with_boundary_inclusive() -> None:
    assert budget.cap_token(_DOC_TOKENS) == 100.0
    assert budget.PRECEDENT_RATIO == 0.10
    ledger = budget.PrecedentLedger()
    ledger.charge("d", 100)
    assert not ledger.verdict("d", _DOC_TOKENS).over_budget  # 等于上限=未越限
    ledger.charge("d", 1)
    v = ledger.verdict("d", _DOC_TOKENS)
    assert v.over_budget and v.spent_token == 101.0 and v.cap_token == 100.0


def test_cap_arithmetic_is_exact_for_small_docs() -> None:
    assert budget.cap_token(3) == 0.3  # 0.30000000000000004 类浮点尾噪须被 round 消掉（可对账）
    ledger = budget.PrecedentLedger()
    ledger.charge("d", 1)
    assert not ledger.verdict("d", 10).over_budget  # 1 ≤ 1.0
    ledger.charge("d", 1)
    assert ledger.verdict("d", 10).over_budget  # 2 > 1.0


def test_rule_path_without_decider_never_charges(tmp_path: Path) -> None:
    ledger = budget.PrecedentLedger()
    outcome = budget.resolve(_canonical(_store(tmp_path)), ledger=ledger)
    assert _deciders(outcome.decision) == {"rule"}
    assert not outcome.human_review and outcome.reason == ""
    assert ledger.spent("doc1") == 0.0  # 未注入③级=不冒充 LLM 判定，判例账恒 0


def test_within_budget_candidate_passes_through(tmp_path: Path) -> None:
    ledger = budget.PrecedentLedger()
    canonical = _canonical(_store(tmp_path))
    outcome = budget.resolve(canonical, ledger=ledger, decider=lambda c: (_candidate(c), 50))
    assert outcome.decision.profile == "llm_semantic_v1"
    assert _deciders(outcome.decision) == {"llm"}
    assert not outcome.human_review
    assert ledger.spent("doc1") == 50.0


def test_over_budget_demotes_to_rule_default_and_marks_review(tmp_path: Path) -> None:
    ledger = budget.PrecedentLedger()
    canonical = _canonical(_store(tmp_path))
    outcome = budget.resolve(canonical, ledger=ledger, decider=lambda c: (_candidate(c), 200))
    rule = chunker.plan(canonical)
    assert outcome.human_review and "D-3" in outcome.reason and "ingest" in outcome.reason  # 计摄取池账
    demoted = outcome.decision
    assert demoted.profile == budget.RULE_DEFAULT_PROFILE == rule.profile
    assert _deciders(demoted) == {"default"}  # D-1 契约内的降级标记位（无须新字段）
    assert [d.node_ids for d in demoted.decisions] == [d.node_ids for d in rule.decisions]  # 降级=回落规则档节点段
    assert ledger.spent("doc1") == 200.0  # 越界消耗照样入账，不回滚成"未发生"


def test_exhausted_doc_stops_touching_fallback(tmp_path: Path) -> None:
    """判例账已耗尽→同文档后续决议不再调用③级（继续烧=兜底反噬成主刀）。"""
    ledger = budget.PrecedentLedger()
    canonical = _canonical(_store(tmp_path))
    calls: list[int] = []

    def decider(c: CanonicalDocument) -> tuple[ChunkDecision, int]:
        calls.append(1)
        return _candidate(c), 200

    assert budget.resolve(canonical, ledger=ledger, decider=decider).human_review
    assert budget.resolve(canonical, ledger=ledger, decider=decider).human_review
    assert len(calls) == 1
    assert ledger.spent("doc1") == 200.0


def test_demoted_record_replays_deterministically(tmp_path: Path) -> None:
    """降级件仍是合法决议记录：同一 canonical 重放=同一批块（D-1 判型与执行分离不因降级失效）。"""
    ledger = budget.PrecedentLedger()
    canonical = _canonical(_store(tmp_path))
    demoted = budget.resolve(canonical, ledger=ledger, decider=lambda c: (_candidate(c), 999)).decision
    again = budget.resolve(canonical, ledger=budget.PrecedentLedger(), decider=lambda c: (_candidate(c), 999))
    assert demoted.model_dump() == again.decision.model_dump()
    assert [c.text for c in chunker.apply(canonical, demoted)] == [
        c.text for c in chunker.apply(canonical, chunker.plan(canonical))
    ]


class _CollectAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


def _pipeline(
    tmp_path: Path, audit: _CollectAudit, ledger: budget.PrecedentLedger, decider: budget.LlmFallbackDecider | None
) -> tuple[IngestPipeline, FileSystemObjectStore]:
    store = _store(tmp_path)
    pipeline = IngestPipeline(
        DevHashEmbedding(dim=8),
        MemoryVectorStore(),
        store,
        PgQueueMq(),
        audit=audit,
        inventory=CanonicalInventory(),
        precedent_ledger=ledger,
        chunk_fallback=decider,
    )
    return pipeline, store


def test_pipeline_archives_demoted_decision_and_alarms(tmp_path: Path) -> None:
    """端到端：③级越 10% 上限→归档决议=降级件（decider=default）+ B2 软告警审计带人审标记，写侧不阻断。"""
    audit = _CollectAudit()
    ledger = budget.PrecedentLedger()
    pipeline, store = _pipeline(tmp_path, audit, ledger, lambda c: (_candidate(c), 200))
    event = pipeline.process_document(
        Document(doc_id="doc9", version=3, domain="hr", acl_tags=["hr"], raw_text="字" * (_DOC_TOKENS * 4))
    )
    restored = ChunkDecision.model_validate_json(store.get("doc9/3#decision"))
    assert all(d.decider == "default" for d in restored.decisions) and restored.profile == budget.RULE_DEFAULT_PROFILE
    assert ledger.spent("doc9") == 200.0
    trips = [e for e in audit.events if e.kind == "breaker_trip"]
    assert len(trips) == 1
    assert trips[0].doc_id == "doc9" and "D-3" in trips[0].detail
    assert trips[0].extra["human_review"] is True and trips[0].extra["action"] == "alarm"
    assert event.doc_id == "doc9"  # 降级不中断摄取（软告警口径）


def test_pipeline_without_fallback_emits_no_demotion_audit(tmp_path: Path) -> None:
    audit = _CollectAudit()
    ledger = budget.PrecedentLedger()
    pipeline, store = _pipeline(tmp_path, audit, ledger, None)
    pipeline.process_document(
        Document(doc_id="doc8", version=1, domain="hr", acl_tags=["hr"], raw_text="字" * (_DOC_TOKENS * 4))
    )
    assert _deciders(ChunkDecision.model_validate_json(store.get("doc8/1#decision"))) == {"rule"}
    assert ledger.spent("doc8") == 0.0
    assert not [e for e in audit.events if e.kind == "breaker_trip"]
