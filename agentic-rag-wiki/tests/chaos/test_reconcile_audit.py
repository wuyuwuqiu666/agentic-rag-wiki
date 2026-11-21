"""M1-b 验收：B5 对账 shadow + FR-MEM-1 审计面（隔离/外呼/越权/发布/fail-loud）。"""

from __future__ import annotations

import pytest
from src.partitions.memory.service.audit_sink import AuditSink
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.gateway.app import App
from src.platform.gateway.audit_adapter import AuditAdapter
from src.platform.gateway.policy_gate import PolicyGate
from src.platform.harness.breaker_rules.loader import load_rules
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.errors import BreakerTripped, ProviderError
from src.platform.providers.rerank_dev import DevLexicalRerank
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Chunk, Document

_LONG = "# 政策\n\n" + "\n\n".join(f"第{i}段 执行档摄取与检索说明文字，长度足以沉淀 wiki 并切成多块。" for i in range(60))


def _container(tmp_path):
    return build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))


def test_b5_reconcile_records_divergence_in_shadow(tmp_path) -> None:
    c = _container(tmp_path)
    c.ingest.process_document(Document(doc_id="d1", domain="hr", acl_tags=["hr"], raw_text=_LONG))
    indexed = c.vectorstore.all_ids()
    assert indexed
    c.vectorstore.delete([indexed[0]])  # 制造清单↔索引背离（删一个已索引块）

    result = c.ingest.reconcile()
    assert result["missing"] >= 1  # 清单应索引 > 索引实际
    trips = [e for e in c.audit.read_all() if e.kind == "breaker_trip" and e.extra["breaker"] == "B5"]
    assert trips and trips[0].extra["shadow"] is True and trips[0].extra["action"] == "observe"


def test_quarantine_emits_audit(tmp_path) -> None:
    c = _container(tmp_path)
    c.ingest.process_document(Document(doc_id="dlp", domain="d", raw_text="前言段落\n\napi_key = SUPERSECRET123"))
    assert any(e.kind == "quarantine" for e in c.audit.read_all())  # DLP 命中→隔离留痕


def test_answer_egress_and_denial_emit_audit(tmp_path) -> None:
    c = _container(tmp_path)
    c.ingest.process_document(Document(doc_id="d1", domain="hr", acl_tags=["hr"], raw_text=_LONG))
    ok = c.app.answer("执行档怎么做", user_acl=["hr"])
    assert not ok.refused
    assert any(e.kind == "external_call" for e in c.audit.read_all())  # R11：LLM 出口=出网点
    denied = c.app.answer("无关问题", user_acl=["outsider"])
    assert denied.refused
    assert any(e.kind == "acl_denied" for e in c.audit.read_all())  # 越权域无命中→拒答且留痕


def test_wiki_publish_emits_audit(tmp_path) -> None:
    c = _container(tmp_path)
    event = c.ingest.process_document(Document(doc_id="d1", domain="hr", acl_tags=["hr"], raw_text=_LONG))
    assert c.wiki.run_once() >= 1
    page = c.wiki.drafts[0]
    c.wiki.submit_for_review(page)
    assert c.wiki.publish(page, event, reviewer="wiki_owner") >= 1
    assert any(e.kind == "wiki_publish" for e in c.audit.read_all())


def test_llm_provider_failure_fails_loud_and_trips_b3(tmp_path) -> None:
    s = Settings(audit_dir=str(tmp_path / "audit"))
    audit = AuditSink(s.audit_dir)
    gate = PolicyGate(load_rules(), audit, shadow_only=s.shadow_breaker_set)  # B3 不在 shadow→硬断
    emb = DevHashEmbedding(dim=32)
    vs = MemoryVectorStore()
    vecs = emb.embed(["块甲内容", "块乙内容"])
    vs.upsert(
        [
            Chunk(chunk_id="a#0", doc_id="a", doc_version=1, seq=0, text="块甲内容", vector=vecs[0]),
            Chunk(chunk_id="b#0", doc_id="b", doc_version=1, seq=0, text="块乙内容", vector=vecs[1]),
        ]
    )

    class BoomLlm:
        def complete(self, prompt: str, max_tokens: int = 512) -> str:
            raise ProviderError("llm 网关不可达")

    app = App(s, emb, vs, DevLexicalRerank(), BoomLlm(), guard=gate, audit=AuditAdapter(audit))
    with pytest.raises(BreakerTripped):  # B3 供给硬断冻结写侧，不静默
        app.answer("块甲内容", user_acl=[])
    events = audit.read_all()
    assert any(e.kind == "provider_fail_loud" for e in events)
    assert any(e.kind == "breaker_trip" and e.extra["breaker"] == "B3" for e in events)
