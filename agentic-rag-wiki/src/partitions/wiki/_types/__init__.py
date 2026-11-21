"""B 区私有类型：triage 判定信号载体（FR-WIKI-4）。

`TriageSignals` = 三类入选信号 + 两类降级信号的**统一输入**，全部为可回放的确定值（不训练模型）。
静态属性/长文来自 canonical 信封；行为信号来自问答侧观测（域内高频提问、agent 多跳仍低置信）；
管理员圈定来自运维。学习回路 M3 仅以**合成信号演练**（FR-MEM-6），故信号可整份注入、判定可复跑。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriageSignals:
    """一页/一文档的 triage 判定输入（默认全零=仅静态属性参与判定，向后兼容旧调用）。"""

    doc_type: str = "other"  # 静态属性：policy/runbook=制度规范类候选；meeting=纪要不沉淀
    token_count: int = 0  # 长文兜底候选（无行为信号时仍可沉淀）
    admin_pinned: bool = False  # 管理员圈定：强制入选，优先级最高
    query_freq: int = 0  # 行为信号：窗口内域内提问命中次数（高频=值得编译）
    low_confidence_hits: int = 0  # 行为信号：agent 多跳后仍低置信的提问数（触发编译）
    recent_hit_delta: int = 0  # 降级信号：提问命中趋势（负=走低）
    source_major_change: bool = False  # 降级信号：源文档大改
