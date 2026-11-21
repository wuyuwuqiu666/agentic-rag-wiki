"""B0–B5 规则合法性机检（护栏产物 #4）：schema + 启用档位 + 阈值单调 + ADR 引用可解析。

`python -m src.platform.harness.breaker_rules.validate`
v1 政策：仅 B1/B3/B5 可 hard；B2=soft、B4=shadow、B0=hard（总闸）。
ADR 引用不止查"存在字段"，还须解析到 `docs/adr/ADR-00N-*.md` 真实文件（追溯断裂即 CI 红——
既防阈值改而无据，也防 adr 挂错号：曾抓到 b0.yaml→ADR-001、b4.yaml→ADR-003 两处错挂）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from src.platform.breaker.rules import Rule
from src.platform.config.settings import Settings
from src.platform.types.breaker import BreakerLevel

_RULES_DIR = Path(__file__).resolve().parent
_ADR_DIR = _RULES_DIR.parents[3] / "docs" / "adr"  # 权威 ADR 活文档目录（repo 根/docs/adr）
# v1 允许 hard 的闸（治复位疲劳：只 3 个硬断 + 总闸）
_HARD_OK = {"B0", "B1", "B3", "B5"}


def check_capacity_single_source(declared: int | float | None, b2_hard: int | float | None) -> str | None:
    """§8.8 容量单一口径不变式：`Settings.inflight_llm_limit`（声明的并发上限）须与 `b2.yaml` B2 `hard`
    （禁改区执法阈）同值——同一"8"两处各表、任一处改而另一处不跟=静默漂移。返回违规串或 None（一致/无法比对）。"""
    if declared is None or b2_hard is None:
        return None
    if float(declared) != float(b2_hard):
        return (
            f"§8.8 容量单一口径漂移：Settings.inflight_llm_limit={declared} ≠ b2.yaml B2 hard={b2_hard}"
            "（并发上限两处须同值，改一处须同步另一处或按 ADR 定档）"
        )
    return None


def _existing_adr_ids() -> set[str]:
    """从 docs/adr 文件名收集已存在的 ADR 编号（形如 ADR-003，取 `ADR-<num>` 两段）。"""
    if not _ADR_DIR.is_dir():
        return set()
    ids: set[str] = set()
    for f in _ADR_DIR.glob("ADR-*.md"):
        parts = f.name.split("-")
        if len(parts) >= 2:
            ids.add(f"{parts[0]}-{parts[1]}")
    return ids


def _coerce(raw: object) -> list[dict[str, object]]:
    return raw if isinstance(raw, list) else [raw]  # type: ignore[list-item]


def check() -> list[str]:
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    b2_inflight_hard: float | None = None
    resolve = _ADR_DIR.is_dir()
    existing = _existing_adr_ids() if resolve else set()
    for path in sorted(_RULES_DIR.glob("*.yaml")):
        for item in _coerce(yaml.safe_load(path.read_text(encoding="utf-8"))):
            adr = item.get("adr") if isinstance(item, dict) else None
            if not adr:
                problems.append(f"{path.name}: 缺 adr 引用（阈值变更须附 ADR）")
            elif resolve and isinstance(adr, str) and adr not in existing:
                problems.append(f"{path.name}: adr 引用 {adr} 在 docs/adr 无对应文件（追溯断裂，须改正或补 ADR）")
            try:
                rule = Rule.model_validate(item)
            except Exception as exc:  # noqa: BLE001 - 汇报解析失败本身
                problems.append(f"{path.name}: 规则 schema 非法：{exc}")
                continue
            if rule.breaker == "B2" and rule.metric == "inflight_llm" and rule.hard is not None:
                b2_inflight_hard = rule.hard
            key = (rule.breaker, rule.metric)
            if key in seen:
                problems.append(f"{path.name}: 重复规则 {key}")
            seen.add(key)
            if rule.level == BreakerLevel.HARD and rule.breaker not in _HARD_OK:
                problems.append(f"{path.name}: {rule.breaker} 在 v1 档不得 hard（仅 {sorted(_HARD_OK)}）")
            if rule.comparator == ">=" and rule.soft is not None and rule.hard is not None and rule.soft > rule.hard:
                problems.append(f"{path.name}: soft({rule.soft}) > hard({rule.hard})，阈值非单调")
    drift = check_capacity_single_source(Settings.model_fields["inflight_llm_limit"].default, b2_inflight_hard)
    if drift:
        problems.append(drift)
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("breaker_rules.validate 违规：")
        for p in problems:
            print("  -", p)
        return 1
    print(f"breaker_rules.validate：{len(list(_RULES_DIR.glob('*.yaml')))} 个规则文件合法，v1 档位政策满足。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
