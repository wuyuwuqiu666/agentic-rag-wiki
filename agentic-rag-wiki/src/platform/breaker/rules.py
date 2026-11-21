"""熔断规则数据模型（规则即数据，由 harness/breaker_rules/*.yaml 注入；内核零 IO）。

内核只消费 Rule 对象，不读文件、不碰时钟（ts 由调用方传入）。
"""

from __future__ import annotations

from pydantic import BaseModel

from src.platform.types.breaker import Action, BreakerId, BreakerLevel


class Rule(BaseModel):
    breaker: BreakerId
    metric: str
    comparator: str = ">="  # ">=" 超阈触发；"<=" 低于阈触发（如可用率）
    soft: float | None = None
    hard: float | None = None
    level: BreakerLevel = BreakerLevel.HARD
    reason: str = ""


# 档位 × 越阈严重度 → 动作（v1：B1/B3/B5 硬断、B2 软告警、B4 shadow）
_ACTION_MAP: dict[BreakerLevel, dict[str, Action]] = {
    BreakerLevel.HARD: {"hard": "block", "soft": "alarm"},
    BreakerLevel.SOFT: {"hard": "alarm", "soft": "alarm"},
    BreakerLevel.SHADOW: {"hard": "observe", "soft": "observe"},
}


def action_for(level: BreakerLevel, severity: str) -> Action:
    return _ACTION_MAP[level][severity]
