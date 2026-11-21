"""参数双级白名单机检（FR-MEM-4，护栏产物 #4a）。

`python -m src.platform.harness.param_class_check`
①yaml 里 safety/business 不重叠且关键安全参数在册；②类型不变式：safety 提案 auto_applicable 必为 False
（跨区三禁之③：C 区学习回路不得自动写安全参数）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from src.platform.types.memory import ParamProposal

_YAML = Path(__file__).resolve().parent / "param_class.yaml"
_MUST_BE_SAFETY = {"reject_threshold", "acl_rules", "breaker_thresholds", "dlp_patterns"}


def check() -> list[str]:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    safety = set(data["safety"])
    business = set(data["business"])
    problems: list[str] = []
    if safety & business:
        problems.append(f"参数归类冲突（既 safety 又 business）：{sorted(safety & business)}")
    missing = _MUST_BE_SAFETY - safety
    if missing:
        problems.append(f"关键安全参数未登记为 safety：{sorted(missing)}")
    # 类型不变式：safety + auto_applicable=True 必须被拒
    try:
        ParamProposal(param="reject_threshold", param_class="safety", auto_applicable=True)
        problems.append("类型不变式失效：safety 提案竟可 auto_applicable")
    except Exception:  # noqa: BLE001 - 期望抛出
        pass
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("param_class_check 违规：")
        for p in problems:
            print("  -", p)
        return 1
    print("param_class_check：双级白名单一致，安全参数禁自动生效不变式成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
