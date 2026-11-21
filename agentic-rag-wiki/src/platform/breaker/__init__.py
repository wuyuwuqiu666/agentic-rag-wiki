"""熔断纯规则内核（零 IO：指标入 → BreakerDecision 出）。单测见本目录 test_*.py。"""

from __future__ import annotations

from src.platform.breaker import pool, slo
from src.platform.breaker.engine import evaluate, evaluate_b0
from src.platform.breaker.rules import Rule, action_for

__all__ = ["Rule", "action_for", "evaluate", "evaluate_b0", "pool", "slo"]
