"""§15 E4「结构化日志带 trace_id 贯通会话」离线代码路径：一次问答会话 = 一个 trace_id。

覆盖会话入口 `AnswerAgent.answer`（ContextVar 盖章）→ 该会话内全部审计事件 + 最终 Answer 共享同一
非空 trace_id；不同会话互不相同；显式传入的 trace_id 被复用。断言的是贯通不变式，非日志内容质量。
"""

from __future__ import annotations

import pytest
from src.partitions.ingest.runtime.ask import AnswerAgent, QaState
from src.platform.config import Settings
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.errors import ProviderError
from src.platform.providers.orchestration_plain import PlainOrchestration
from src.platform.providers.rerank_dev import DevLexicalRerank
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import Chunk


class _RecAudit:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:  # noqa: ANN001 - AuditPort
        self.events.append(event)


def _agent(llm, audit) -> AnswerAgent:  # noqa: ANN001
    return AnswerAgent(
        Settings(),
        DevHashEmbedding(dim=8),
        MemoryVectorStore(),
        DevLexicalRerank(),
        llm,
        PlainOrchestration(),
        audit=audit,
    )


def _fixed_gather(state: QaState, *, ranked: list[Chunk] | None = None, visible: bool = True) -> QaState:
    state.ranked = ranked or []
    state.confidence = 1.0
    state.visible = visible
    state.hops = 1
    return state


class _GroundedLlm:
    def complete(self, prompt: str, max_tokens: int = 512) -> str:  # noqa: ARG002
        return "依据[d1/1#0]规定见薪酬附录。"


class _SecretLlm:
    def complete(self, prompt: str, max_tokens: int = 512) -> str:  # noqa: ARG002
        return "依据[d1/1#0]记录口令 api_key=SUPERSECRET-9 请核对。"


_CHUNK = Chunk(chunk_id="d1/1#0", doc_id="d1", doc_version=1, seq=0, text="薪酬附录", acl_tags=["hr"])


def test_answer_carries_nonempty_trace_id() -> None:
    audit = _RecAudit()
    agent = _agent(_GroundedLlm(), audit)
    agent._gather = lambda s: _fixed_gather(s, ranked=[_CHUNK])  # type: ignore[method-assign]
    ans = agent.answer("薪酬附录在哪", user_acl=["hr"])
    assert ans.trace_id  # 会话出口不再为空（此前声明但从不赋值=死字段）


def test_all_events_in_one_session_share_the_answer_trace() -> None:
    audit = _RecAudit()
    agent = _agent(_SecretLlm(), audit)
    agent._gather = lambda s: _fixed_gather(s, ranked=[_CHUNK])  # type: ignore[method-assign]
    ans = agent.answer("薪酬附录在哪", user_acl=["hr"])
    kinds = {e.kind for e in audit.events}
    assert {"external_call", "quarantine"} <= kinds  # 一次会话发多类事件
    assert all(e.trace_id == ans.trace_id and e.trace_id for e in audit.events)


def test_distinct_sessions_get_distinct_traces() -> None:
    audit = _RecAudit()
    agent = _agent(_GroundedLlm(), audit)
    agent._gather = lambda s: _fixed_gather(s, ranked=[_CHUNK])  # type: ignore[method-assign]
    first = agent.answer("薪酬附录在哪", user_acl=["hr"])
    second = agent.answer("薪酬附录在哪", user_acl=["hr"])
    assert first.trace_id and second.trace_id
    assert first.trace_id != second.trace_id
    assert all(e.trace_id in {first.trace_id, second.trace_id} for e in audit.events)  # 事件不串号


def test_explicit_trace_id_is_reused_for_events_and_answer() -> None:
    audit = _RecAudit()
    agent = _agent(_GroundedLlm(), audit)
    agent._gather = lambda s: _fixed_gather(s, ranked=[_CHUNK])  # type: ignore[method-assign]
    ans = agent.answer("薪酬附录在哪", user_acl=["hr"], trace_id="sess-42")
    assert ans.trace_id == "sess-42"
    assert all(e.trace_id == "sess-42" for e in audit.events)


def test_refused_session_still_stamps_trace() -> None:
    audit = _RecAudit()
    agent = _agent(_GroundedLlm(), audit)
    agent._gather = lambda s: _fixed_gather(s, ranked=[], visible=False)  # type: ignore[method-assign]
    ans = agent.answer("无可见命中", user_acl=["outsider"])
    assert ans.refused and ans.trace_id  # 拒答会话同样盖章
    assert any(e.kind == "acl_denied" and e.trace_id == ans.trace_id for e in audit.events)


def test_trace_survives_provider_failure_event() -> None:
    audit = _RecAudit()
    agent = _agent(_GroundedLlm(), audit)

    # _generate 内部调 llm.complete；令其抛错以触发 provider_fail_loud（gather 保持正常召回）
    agent._gather = lambda s: _fixed_gather(s, ranked=[_CHUNK])  # type: ignore[method-assign]
    agent._generate = lambda q, r: (_ for _ in ()).throw(ProviderError("llm 出口炸了"))  # type: ignore[method-assign]
    with pytest.raises(ProviderError):
        agent.answer("薪酬附录在哪", user_acl=["hr"])
    assert any(e.kind == "provider_fail_loud" and e.trace_id for e in audit.events)  # 异常前事件仍带会话 trace


def test_reset_clears_contextvar_after_session() -> None:
    from src.partitions.ingest.runtime import ask as ask_mod

    audit = _RecAudit()
    agent = _agent(_GroundedLlm(), audit)
    agent._gather = lambda s: _fixed_gather(s, ranked=[_CHUNK])  # type: ignore[method-assign]
    agent.answer("薪酬附录在哪", user_acl=["hr"])
    assert ask_mod._trace_var.get() == ""  # finally 复位：无残留，防跨会话串号
