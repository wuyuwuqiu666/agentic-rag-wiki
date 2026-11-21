"""一级契约向后兼容机检（§15 DoD-E E3 / R7 冻结前守护）。

`python -m src.platform.harness.contract_compat`            # 校验：不得删字段/改类型（相对已提交基线）
`python -m src.platform.harness.contract_compat --update`   # 契约有意演进时重写基线（须配 ADR，见 R7）

本机无 HTTP 面（gateway 为进程内薄壳），**pydantic 一级契约即跨区事实源的对外契约**（A/B/C 三区共享），
故 E3"API 契约向后兼容检查"在此落在 types 契约快照上：新增字段/模型=向后兼容放行；
删除字段或改类型=破坏兼容、CI 红（与 Alembic 迁移/openapi-diff 同理，后者需真实 DB 属实机放行项）。
schemas.validate 守"必填字段不缺失"，本脚本守"整张契约面不被静默收窄"（含可选字段与类型）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.platform.harness.schemas.validate import _MODELS

_BASELINE = Path(__file__).resolve().parent / "contract_baseline.json"


def snapshot() -> dict[str, dict[str, str]]:
    """当前契约面：{模型: {字段: 类型注解字符串}}（类型取 str(annotation)，同环境稳定可比）。"""
    return {
        name: {field: str(info.annotation) for field, info in model.model_fields.items()}
        for name, model in _MODELS.items()
    }


def diff(baseline: dict[str, dict[str, str]], current: dict[str, dict[str, str]]) -> list[str]:
    """纯判定：只拦破坏兼容的变更（删模型/删字段/改类型）；新增一律放行=向后兼容。"""
    problems: list[str] = []
    for model_name, base_fields in baseline.items():
        cur_fields = current.get(model_name)
        if cur_fields is None:
            problems.append(f"契约模型被移除（破坏兼容）：{model_name}")
            continue
        for field, base_type in base_fields.items():
            if field not in cur_fields:
                problems.append(f"字段被删除（破坏兼容）：{model_name}.{field}")
            elif cur_fields[field] != base_type:
                problems.append(f"字段类型变更（破坏兼容）：{model_name}.{field} {base_type} -> {cur_fields[field]}")
    return problems


def check() -> list[str]:
    if not _BASELINE.exists():
        return [f"契约基线缺失：{_BASELINE.name} 不存在，先运行 `--update` 生成并提交"]
    baseline: dict[str, dict[str, str]] = json.loads(_BASELINE.read_text(encoding="utf-8"))
    return diff(baseline, snapshot())


def update_baseline() -> None:
    _BASELINE.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if "--update" in argv:
        update_baseline()
        print(f"contract_compat：基线已重写 → {_BASELINE.name}（契约演进须同步走 ADR，R7）。")
        return 0
    problems = check()
    if problems:
        print("contract_compat 违规（契约面被静默收窄，破坏跨区向后兼容）：")
        for p in problems:
            print("  -", p)
        return 1
    print("contract_compat：一级契约面向后兼容，无字段删除/类型变更。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
