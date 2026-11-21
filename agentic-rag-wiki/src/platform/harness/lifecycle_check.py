"""文件生命周期保留策略机检（§9 产物 #4a、E8「审计日志保留期显式配置」离线核心）。

`python -m src.platform.harness.lifecycle_check`
`harness/file_lifecycle.yaml` 此前仅在 `audit_sink` docstring 被提及、无任何校验器执法（§9 #4a
声称"提交前 + 运行时写入前机检"实则查无产物=同 deps_governance/secret_scan 那类"声称有门、实则无码"）。
本检补齐：①retention 条目 schema（max_age_days 正整数、action∈{delete,archive}）；②append-only 存储
（审计 NFR-5、金标尺 FR-MEM-5）必须显式登记在 protected_append_only=「保留期显式配置」的机检口径；
③不得既受保护又被 retention 自动删（否则清理作业静默删审计=数据丢失风险）；④漂移防护——审计落点
`Settings.audit_dir` 实际路径须在保护清单内（改默认指向未保护路径即红）。
`is_protected()` 系运行时清理作业删除前须咨询的单一口径真值源（纯函数可单测；真实 cron/清理调度=放行档）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from src.platform.config.settings import Settings

_YAML = Path(__file__).resolve().parent / "file_lifecycle.yaml"
_VALID_ACTIONS = {"delete", "archive"}
# append-only 存储：审计（NFR-5 append-only）与金标尺（FR-MEM-5 只读）须显式登记为"永不自动删"。
_APPEND_ONLY_STORES = ("workspace/audit", "tests/eval")


def load(path: Path = _YAML) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _norm(p: str) -> str:
    return str(p).rstrip("/")


def _under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent + "/")


def is_protected(path: str, cfg: dict[str, Any] | None = None) -> bool:
    """运行时清理作业删除前须咨询的单一口径：命中 protected_append_only 前缀=永不自动删。"""
    data = cfg if cfg is not None else load()
    prot = {_norm(x) for x in (data.get("protected_append_only") or [])}
    target = _norm(path)
    return any(_under(target, p) for p in prot)


def check() -> list[str]:
    data = load()
    problems: list[str] = []
    retention = data.get("retention", {})
    protected = data.get("protected_append_only")

    if not isinstance(retention, dict):
        problems.append("retention 须为 路径→策略 映射")
        retention = {}
    for p, rule in retention.items():
        if not isinstance(rule, dict):
            problems.append(f"retention[{p}] 须为映射（含 max_age_days/action）")
            continue
        days = rule.get("max_age_days")
        action = rule.get("action")
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            problems.append(f"retention[{p}].max_age_days 须为正整数，实为 {days!r}")
        if action not in _VALID_ACTIONS:
            problems.append(f"retention[{p}].action={action!r} 须∈{sorted(_VALID_ACTIONS)}")

    if not isinstance(protected, list) or not protected:
        problems.append("protected_append_only 须为非空列表（append-only 存储显式登记）")
        protected = protected if isinstance(protected, list) else []
    prot = {_norm(x) for x in protected}

    # 冲突：既受 append-only 保护又被 retention 自动删（清理作业会静默删审计=数据丢失风险）。
    for p in retention:
        pn = _norm(p)
        if any(_under(pn, q) or _under(q, pn) for q in prot):
            problems.append(f"冲突：{p} 同时在 retention 与 protected_append_only（append-only 存储不得自动删）")

    # E8 保留期显式配置不变式：审计/金标两类 append-only 存储必须登记在保护清单。
    for store in _APPEND_ONLY_STORES:
        if store not in prot:
            problems.append(f"E8 保留期未显式配置：append-only 存储 '{store}' 不在 protected_append_only")

    # 漂移防护：审计实际落点（Settings.audit_dir 默认值）须被覆盖，防改指向未保护路径后静默可删。
    audit_dir = _norm(Settings.model_fields["audit_dir"].default)
    if not any(_under(audit_dir, q) or _under(q, audit_dir) for q in prot):
        problems.append(f"审计落点 Settings.audit_dir='{audit_dir}' 未被 protected_append_only 覆盖")

    return problems


def main() -> int:
    problems = check()
    if problems:
        print("lifecycle_check 违规：")
        for p in problems:
            print("  -", p)
        return 1
    print("lifecycle_check：保留策略 schema 合法，append-only 存储（审计/金标）已显式登记为永不自动删。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
