"""§15 E4「LLM 调用逐次计 token 成本」离线代码路径：每次出口调用逐次记账并入审计事件。

真·tokenizer 计数与云侧 usage/成本=实机放行项；本文件只锁"逐次计数管道"这一不变式（可对账、确定性）。
"""

from __future__ import annotations

from src.partitions.ingest.runtime.ask import AnswerAgent, QaState
from src.partitions.ingest.service.ask import cost
from src.platform.config import Settings
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.llm_dev import DevEchoLlm
from src.platform.providers.orchestration_plain import PlainOrchestration
from src.platform.providers.rerank_dev import DevLexicalRerank
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Chunk


def test_estimate_tokens_is_len_div_four_and_nonnegative() -> None:
    assert cost.estimate_tokens("") == 0
    assert cost.estimate_tokens("abcd") == 1
    assert cost.estimate_tokens("abcdefghij") == 2  # 10//4
    assert cost.estimate_tokens("abc") == 0  # 向下取整、非负


def test_account_splits_prompt_and_completion_and_totals() -> None:
    c = cost.account("12345678", "abcd")  # 8//4=2, 4//4=1
    assert c.prompt_tokens == 2 and c.completion_tokens == 1
    assert c.total_tokens == 3


def test_est_money_uses_per_1k_prices() -> None:
    c = cost.CallCost(prompt_tokens=1000, completion_tokens=2000)
    assert c.est_money(per_1k_in=0.5, per_1k_out=1.0) == 0.5 * 1 + 1.0 * 2
    # dev 单价 0 → 成本 0（无云侧计费，管道仍计数）
    assert c.est_money(per_1k_in=0.0, per_1k_out=0.0) == 0.0


class _RecAudit:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:  # noqa: ANN001 - AuditPort
        self.events.append(event)


def _agent(settings: Settings, audit: _RecAudit) -> AnswerAgent:
    return AnswerAgent(
        settings,
        DevHashEmbedding(dim=8),
        MemoryVectorStore(),
        DevLexicalRerank(),
        DevEchoLlm(),
        PlainOrchestration(),
        audit=audit,
    )


def test_answer_emits_per_call_token_cost_in_audit() -> None:
    settings = Settings(llm_price_per_1k_in=0.5, llm_price_per_1k_out=1.0)
    audit = _RecAudit()
    agent = _agent(settings, audit)
    chunk = Chunk(chunk_id="d1/1#0", doc_id="d1", doc_version=1, seq=0, text="薪酬与融合规定", acl_tags=["hr"])

    def fake_gather(state: QaState) -> QaState:
        state.ranked = [chunk]
        state.confidence = 1.0
        state.visible = True
        state.hops = 1
        return state

    agent._gather = fake_gather  # type: ignore[method-assign]
    ans = agent.answer("薪酬制度如何规定", user_acl=["hr"])

    assert not ans.refused
    egress = [e for e in audit.events if e.kind == "external_call"]
    assert len(egress) == 1  # 一次会话一次出口调用→逐次一条
    ex = egress[0].extra
    assert ex["prompt_tokens"] > 0 and ex["completion_tokens"] > 0
    assert ex["total_tokens"] == ex["prompt_tokens"] + ex["completion_tokens"]
    expected = ex["prompt_tokens"] / 1000 * 0.5 + ex["completion_tokens"] / 1000 * 1.0
    assert abs(ex["est_cost"] - expected) < 1e-12
