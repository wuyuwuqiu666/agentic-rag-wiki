"""C 区私有类型：学习回路的信号快照（FR-MEM-3 在线信号→画像聚合的入口形态）。

单机期=合成信号演练（FR-MEM-6）：快照是可回放的确定值，学习回路只据此出提案、不落任何自动生效。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SignalSnapshot:
    """一段窗口内的问答侧观测聚合（样本指针留 evidence，正文落文件不入上下文）。"""

    sample_size: int
    refusal_rate: float  # 拒答占比（安全参数 reject_threshold 的画像信号）
    low_conf_rate: float  # 低置信占比（业务参数 answer_topn 的画像信号）
    evidence: list[str] = field(default_factory=list)  # 触发样本的指针（审计/日志 id，非原文）
