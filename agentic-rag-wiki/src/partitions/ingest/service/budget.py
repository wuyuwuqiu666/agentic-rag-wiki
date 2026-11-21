"""D-3 判例预算（FR-ING-7 第③级 LLM 兜底 × B2）：判定内核=纯函数 + 按文档可重放账本。

条款口径（PRD §8.5 B2 行 / 附录 C v0.6.3 D-3）：第③级 LLM 兜底判型的消耗**计摄取池账**，单文档判例
token 上限 = 该文档 token 的 10%；**超限即该文档判例作废→降级为规则默认 profile 并标记人审**
（防"兜底反噬成主刀"）。

执法点：`resolve` 是三级漏斗出口的决议入口——降级件复用 D-1 决议契约的 `decider="default"` 作人审标记
（随 doc_version 归档，重放时仍是降级件，无需新字段），上报复用 B2 软告警口径（alarm，不阻断）。判例消耗
由调用方按 §15 E4 逐次成本口径（`ask.cost.account`）算好后交给第③级钩子，全链同一 token 计数口径。

诚实边界：真实第③级 decider 与真实 tokenizer 逐次 usage 回传=实机放行项（AGENTS §8）。未注入 decider 时
本档走规则档、判例账恒为 0，绝不冒充 LLM 判定；降级与记账分支由契约测试以合成 decider 覆盖。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from src.partitions.ingest.service import chunker
from src.platform.types import CanonicalDocument, ChunkDecision

RULE_DEFAULT_PROFILE = "rule_structure_v1"  # D-2 块型默认档位表的规则档主 profile（= chunker.plan 输出）
PRECEDENT_RATIO = 0.10  # D-3：单文档判例 token 上限=该文档 token 的 10%

# 第③级钩点契约：输入 canonical，输出 (候选决议, 本次判例消耗 token)。真实 LLM decider 在组合根装配。
LlmFallbackDecider = Callable[[CanonicalDocument], tuple[ChunkDecision, int]]


class PrecedentVerdict(NamedTuple):
    over_budget: bool
    cap_token: float
    spent_token: float
    reason: str


class DecisionOutcome(NamedTuple):
    decision: ChunkDecision
    human_review: bool  # True=判例超限被降级，调用方须发审计（不得静默）
    reason: str = ""


def cap_token(doc_token_count: int) -> float:
    """单文档判例上限：文档 token × 10%（round 到 9 位消浮点尾噪，保证可对账等值比较）。"""
    return round(max(doc_token_count, 0) * PRECEDENT_RATIO, 9)


class PrecedentLedger:
    """摄取池判例子账（按 doc_id 累计逐次消耗）：账本即状态，判定全在纯函数里，可重放可对账。"""

    def __init__(self, *, pool: str = "ingest") -> None:
        self.pool = pool  # D-3「计摄取池账」：账目归属池，越限理由与审计一并带上，供分池预算核账
        self._spent: dict[str, float] = {}

    def spent(self, doc_id: str) -> float:
        return self._spent.get(doc_id, 0.0)

    def charge(self, doc_id: str, token: int) -> float:
        """逐次记账：越界的消耗也必须入账（判定在后），不得回滚成"未发生"。"""
        cur = round(self._spent.get(doc_id, 0.0) + max(token, 0), 9)
        self._spent[doc_id] = cur
        return cur

    def verdict(self, doc_id: str, doc_token_count: int) -> PrecedentVerdict:
        spent, cap = self.spent(doc_id), cap_token(doc_token_count)
        over = spent > cap
        reason = (
            f"B2 判例预算（D-3）越限：{self.pool} 池账下文档判例已耗 {spent} token > 上限 {cap}"
            f"（文档 {doc_token_count} token × {PRECEDENT_RATIO:.0%}）"
        )
        return PrecedentVerdict(over, cap, spent, reason)


def demote(rule: ChunkDecision) -> ChunkDecision:
    """判例作废→回落规则默认 profile：复用 D-1 决议契约的 decider="default" 作人审标记位。

    node 级决议（ADR-010）下判级逐段记录，故降级件=把**每一条**节点段的 decider 改写为 "default"
    （不留 doc 级第二套口径：读降级只需看任一段，重放只需看 profile）。
    """
    return rule.model_copy(update={"profile": RULE_DEFAULT_PROFILE}).with_decider("default")


def resolve(
    canonical: CanonicalDocument, *, ledger: PrecedentLedger, decider: LlmFallbackDecider | None = None
) -> DecisionOutcome:
    """三级漏斗出口的 D-3 门：规则档恒为基线；③级候选须先入判例账，越 10% 上限即作废并降级。

    判例账已耗尽的文档**不再触达 decider**（继续消耗=兜底反噬），直接给降级件。
    """
    rule = chunker.plan(canonical)
    if decider is None:
        return DecisionOutcome(rule, False)
    doc_id, doc_tokens = canonical.doc_id, canonical.token_count
    before = ledger.verdict(doc_id, doc_tokens)
    if before.over_budget:
        return DecisionOutcome(demote(rule), True, before.reason)
    candidate, token = decider(canonical)
    ledger.charge(doc_id, token)
    after = ledger.verdict(doc_id, doc_tokens)
    if after.over_budget:
        return DecisionOutcome(demote(rule), True, after.reason)
    return DecisionOutcome(candidate, False)
