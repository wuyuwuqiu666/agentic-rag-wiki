"""熔断契约：指标入 → BreakerDecision 出（P1 纯内核的数据面）。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

BreakerId = Literal["B0", "B1", "B2", "B3", "B4", "B5"]
Action = Literal["block", "degrade", "alarm", "observe", "none"]


class BreakerLevel(StrEnum):
    HARD = "hard"  # 硬断：阻断流水线
    SOFT = "soft"  # 软告警：只告警+审计
    SHADOW = "shadow"  # 影子：只记不发


class MetricSample(BaseModel):
    breaker: BreakerId
    metric: str
    value: float
    window: str = "instant"
    partition: str = "platform"
    ts: float = 0.0
    labels: dict[str, str] = Field(default_factory=dict)


class BreakerDecision(BaseModel):
    breaker: BreakerId
    tripped: bool
    action: Action = "none"
    level: BreakerLevel = BreakerLevel.HARD
    reason: str = ""
    metric: str = ""
    value: float = 0.0
    threshold: float | None = None
    escalated_from: list[str] = Field(default_factory=list)  # B0 升级来源（v0.6 分级）
    ts: float = 0.0


class TripRecord(BaseModel):
    """触发史（B0 升级计数与 R9 复位疲劳监控的数据源）。"""

    breaker: BreakerId
    tripped_at: float
    reason: str = ""
    reset_by: str = ""
    reset_reason: str = ""
    reset_at: float | None = None
