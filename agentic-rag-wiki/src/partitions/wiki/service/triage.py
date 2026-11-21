"""B 区 triage 判定器（FR-WIKI-4：纯规则、可单测、可回放，不训练模型）。

入选=三类信号任一命中（管理员圈定 > 静态属性 > 行为信号 > 长文兜底）；纪要类默认不沉淀。
降级=已发布页的提问命中走低或源文档大改 → 退草稿重编译。判定与 A 区"能不能入库"分离（PRD 附录 E）。
真·行为信号在学习回路 M3 仅以合成信号演练（FR-MEM-6），故本模块只吃 `TriageSignals` 确定值。
"""

from __future__ import annotations

from src.partitions.wiki._types import TriageSignals

_COMPILE_DOC_TYPES = {"policy", "runbook"}  # 制度/规范/runbook=候选（静态属性）
_EXCLUDE_DOC_TYPES = {"meeting"}  # 纪要/草稿类不进
_HIGH_QUERY_FREQ = 5  # 域内高频提问阈值（行为信号）
_LOWCONF_TRIGGER = 1  # agent 多跳仍低置信≥此数即触发编译（行为信号）
_LONG_DOC_TOKENS = 200  # 无行为信号时的长文兜底
_HIT_DECLINE = -3  # 提问命中趋势低于此值=走低（降级信号）


def should_compile(signals: TriageSignals) -> tuple[bool, str]:
    """入选判定：返回 (是否编译, 依据)。依据写入 `WikiPage.triage_reason` 供回放核对。"""
    if signals.admin_pinned:
        return True, "管理员圈定=强制入选"
    if signals.doc_type in _EXCLUDE_DOC_TYPES:
        return False, f"{signals.doc_type} 类不沉淀"
    if signals.doc_type in _COMPILE_DOC_TYPES:
        return True, f"制度规范类 doc_type={signals.doc_type}"
    if signals.low_confidence_hits >= _LOWCONF_TRIGGER:
        return True, f"agent 低置信 {signals.low_confidence_hits} 次→触发编译"
    if signals.query_freq >= _HIGH_QUERY_FREQ:
        return True, f"域内高频提问 {signals.query_freq} 次"
    if signals.token_count >= _LONG_DOC_TOKENS:
        return True, f"长文 token={signals.token_count} 兜底沉淀"
    return False, "未达沉淀门槛"


def should_downgrade(signals: TriageSignals) -> tuple[bool, str]:
    """降级判定（仅作用于已发布页）：命中走低或源大改 → 退草稿。"""
    if signals.source_major_change:
        return True, "源文档大改→退草稿重编译"
    if signals.recent_hit_delta <= _HIT_DECLINE:
        return True, f"提问命中走低 Δ={signals.recent_hit_delta}→退草稿"
    return False, ""
