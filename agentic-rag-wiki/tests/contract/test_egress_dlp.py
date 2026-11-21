"""§12 生成后门 / §8.5 B4 出口：答案出口 DLP 内容闸（离线代码路径）。

入口（`admission` 隔离含密块）与出口（`AnswerAgent` 脱敏答案）经 `service.dlp` 单一口径同源。
dev 档确定性模式集=验机械链路，真·结构化 DLP（PII/卡号/证件、可域配置）为外部供给=实机放行项。
"""

from __future__ import annotations

import pytest
from src.partitions.ingest.runtime.ask import AnswerAgent, QaState
from src.partitions.ingest.service import admission, dlp
from src.partitions.ingest.service.ask import cache as cache_mod
from src.platform.config import Settings
from src.platform.providers.cache_memory import MemoryCache
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.orchestration_plain import PlainOrchestration
from src.platform.providers.rerank_dev import DevLexicalRerank
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Chunk, Document


def test_redact_masks_each_secret_and_counts() -> None:
    text = "正文 api_key: SUPER123 与口令 passwd=  hunter2 结尾"
    clean, hits = dlp.redact(text)
    assert hits == 2
    assert "SUPER123" not in clean and "hunter2" not in clean
    assert dlp._MASK in clean and "正文" in clean  # 非密文原样保留


def test_redact_is_noop_on_benign() -> None:
    clean, hits = dlp.redact("向量检索与 BM25 融合的执行档说明。")
    assert hits == 0 and clean == "向量检索与 BM25 融合的执行档说明。"


@pytest.mark.parametrize("kw", ["api_key", "apikey", "secret", "passwd", "password", "token"])
def test_pattern_covers_keyword_set(kw: str) -> None:
    assert dlp.SECRET_RE.search(f"{kw}: VALUE123")


def test_ingress_and_egress_share_one_source_of_truth() -> None:
    """入口准入 import 的就是出口那一份正则——防入口拦得住、出口漏得出去（口径漂移=泄露通道）。"""
    assert admission.dlp is dlp
    assert admission.dlp.SECRET_RE is dlp.SECRET_RE
    # 含密块入口必被隔离（不入库），同源出口必脱敏（不外泄）
    secret = Chunk(chunk_id="d#0", doc_id="d", doc_version=1, seq=0, text="配置 token=ABCDEF", acl_tags=["hr"])
    res = admission.admit([secret])
    assert res.accepted == [] and res.quarantined == [secret]


class _RecAudit:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:  # noqa: ANN001 - AuditPort
        self.events.append(event)


class _RecGuard:
    def __init__(self) -> None:
        self.checks: list[tuple[str, str]] = []

    def check(self, metric: str, value: float, breaker: str, partition: str = "platform") -> None:  # noqa: ARG002
        self.checks.append((metric, breaker))

    def ensure_write_allowed(self) -> None:  # GuardPort 协议占位，读侧不经此
        return None


class _SecretLlm:
    def __init__(self, chunk_id: str) -> None:
        self._cid = chunk_id

    def complete(self, prompt: str, max_tokens: int = 512) -> str:  # noqa: ARG002
        return f"依据[{self._cid}]记录口令 api_key=SUPERSECRET-9 请核对"


def _agent(**ports) -> AnswerAgent:  # noqa: ANN003, ANN001
    return AnswerAgent(
        Settings(),
        DevHashEmbedding(dim=8),
        MemoryVectorStore(),
        DevLexicalRerank(),
        ports["llm"],
        PlainOrchestration(),
        guard=ports.get("guard"),
        audit=ports.get("audit"),
        cache_store=ports.get("cache"),
    )


def test_answer_egress_redacts_secret_and_skips_cache() -> None:
    """接地答案夹带明文口令→出口脱敏后仍呈现（shadow 不阻断会话）、发 B4 信号、且绝不入缓存。"""
    audit, guard, cache = _RecAudit(), _RecGuard(), MemoryCache()
    chunk = Chunk(chunk_id="d1/1#0", doc_id="d1", doc_version=1, seq=0, text="薪酬与融合规定", acl_tags=["hr"])
    agent = _agent(llm=_SecretLlm("d1/1#0"), audit=audit, guard=guard, cache=cache)

    def fake_gather(state: QaState) -> QaState:  # 固定召回/排序，只验出口 DLP 接线
        state.ranked = [chunk]
        state.confidence = 1.0
        state.visible = True
        state.hops = 1
        return state

    agent._gather = fake_gather  # type: ignore[method-assign]
    q = "薪酬制度如何规定"
    ans = agent.answer(q, user_acl=["hr"])

    assert ans.refused is False  # v1 shadow=只脱敏不拒答
    assert "SUPERSECRET-9" not in ans.text and dlp._MASK in ans.text
    assert ans.citations and ans.citations[0].chunk_id == "d1/1#0"  # 接地引用保留
    assert ("injection_score", "B4") in guard.checks  # 出口触发 B4 信号（shadow 记不拦）
    assert any(e.kind == "quarantine" for e in audit.events)  # 出口 DLP 审计（与入口同 kind 口径）
    # §15 E8 DLP 规则版本化：出口隔离审计贴规则版本戳（可回放"哪套规则做的脱敏"）
    qev = next(e for e in audit.events if e.kind == "quarantine")
    assert qev.extra.get("dlp_rules_version") == dlp.RULES_VERSION
    assert cache.get(cache_mod.cache_key(q, ["hr"])) is None  # 脱敏件不入缓存


def test_dlp_rules_version_is_stamped_and_shared_ingress_egress(tmp_path) -> None:
    """入口隔离与出口脱敏同贴一个版本戳（§15 E8"规则版本化"），且随规则进位可对账——非两套漂移口径。"""
    from src.platform.config import Settings as _S
    from src.platform.gateway import build_container

    assert dlp.ruleset_tag() == {"dlp_rules_version": dlp.RULES_VERSION}
    assert isinstance(dlp.RULES_VERSION, str) and dlp.RULES_VERSION

    c = build_container(_S(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))
    # 入口：含明文口令的块被 admission 隔离 → pipeline 落 quarantine 审计并带版本
    c.ingest.process_document(
        Document(doc_id="sec1", domain="hr", acl_tags=["hr"], raw_text="# 配置\n\n段落一 api_key: SUPERVALUE123 结尾")
    )
    ingress = [e for e in c.admin.replay_audit(kinds={"quarantine"})]
    assert ingress and all(e.extra.get("dlp_rules_version") == dlp.RULES_VERSION for e in ingress)


def test_clean_answer_still_caches() -> None:
    """无密文接地答案→正常入缓存（证明准入门未被 DLP 逻辑误伤）。"""
    cache = MemoryCache()

    class _CleanLlm:
        def complete(self, prompt: str, max_tokens: int = 512) -> str:  # noqa: ARG002
            return "依据[d1/1#0]规定见薪酬附录"

    chunk = Chunk(chunk_id="d1/1#0", doc_id="d1", doc_version=1, seq=0, text="薪酬附录", acl_tags=["hr"])
    agent = _agent(llm=_CleanLlm(), cache=cache)

    def fake_gather(state: QaState) -> QaState:
        state.ranked = [chunk]
        state.confidence = 1.0
        state.visible = True
        state.hops = 1
        return state

    agent._gather = fake_gather  # type: ignore[method-assign]
    q = "薪酬附录在哪"
    ans = agent.answer(q, user_acl=["hr"])

    assert not ans.refused
    assert cache.get(cache_mod.cache_key(q, ["hr"])) is not None
