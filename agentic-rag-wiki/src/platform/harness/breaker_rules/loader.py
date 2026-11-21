"""breaker 规则加载器（IO 只在此，内核保持零 IO）：harness/breaker_rules/*.yaml → Rule 列表。

组合根据此构造运行期熔断闸；阈值/启用档位改动仍由 validate.py 机检。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.platform.breaker.rules import Rule

_RULES_DIR = Path(__file__).resolve().parent


def load_rules(directory: Path | None = None) -> list[Rule]:
    rules: list[Rule] = []
    for path in sorted((directory or _RULES_DIR).glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        rules.extend(Rule.model_validate(item) for item in items)
    return rules
