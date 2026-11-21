"""覆盖率门禁机检（护栏产物 / PRD §15 E7）：规则覆盖率 ≥90%、其余 ≥70%。

`python -m src.platform.harness.coverage_gate`
输入=pytest-cov 产出的 `coverage.json`（CI 步骤：`pytest --cov=src --cov-report=json:coverage.json`）。
分档：熔断判定内核 `src/platform/breaker` 与问答规则 `src/partitions/ingest/service/ask`
是"规则即代码"的核心，须 ≥90%；整体 ≥70%。coverage.json 缺失=fail-loud（未跑覆盖率=未验收）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 仓库根为 pythonpath，coverage.json 约定落根目录（CI 可覆写 COVERAGE_JSON）。
_DEFAULT_JSON = Path(__file__).resolve().parents[3] / "coverage.json"
# 高门槛包（PRD E7 点名）：前缀 → 最低覆盖率
_HIGH: dict[str, float] = {
    "src/platform/breaker": 90.0,
    "src/partitions/ingest/service/ask": 90.0,
}
_OVERALL_MIN = 70.0


def _pct(files: dict[str, dict], prefix: str) -> tuple[float | None, int, int]:
    st = cov = 0
    for path, entry in files.items():
        if path.replace("\\", "/").startswith(prefix):
            s = entry.get("summary", {})
            st += s.get("num_statements", 0)
            cov += s.get("covered_lines", 0)
    if st == 0:
        return (None, 0, 0)
    return (cov / st * 100.0, cov, st)


def evaluate(cov: dict) -> list[str]:
    """纯判定：输入 coverage.json 解析后的 dict，返回违规清单（可单测，无 IO）。"""
    files: dict[str, dict] = cov.get("files", {})
    problems: list[str] = []
    for prefix, floor in _HIGH.items():
        pct, c, s = _pct(files, prefix)
        if pct is None:
            problems.append(f"{prefix}：无语句被计入覆盖率（路径未匹配或未插桩），分档门禁无法验证")
        elif pct < floor:
            problems.append(f"{prefix}：覆盖率 {pct:.1f}%（{c}/{s}）< 门槛 {floor:.0f}%")
    totals = cov.get("totals", {}).get("percent_covered")
    if totals is None:
        problems.append("coverage.json 缺 totals.percent_covered")
    elif float(totals) < _OVERALL_MIN:
        problems.append(f"整体覆盖率 {float(totals):.1f}% < 门槛 {_OVERALL_MIN:.0f}%")
    return problems


def main() -> int:
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_JSON
    if not json_path.is_file():
        print(
            f"coverage_gate：未找到 {json_path}。覆盖率属验收项，须先跑 "
            "`pytest --cov=src --cov-report=json:{json_path}`（fail-loud：未测=未过）。"
        )
        return 1
    try:
        cov = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"coverage_gate：coverage.json 解析失败：{exc}")
        return 1
    problems = evaluate(cov)
    if problems:
        print("coverage_gate 违规：")
        for p in problems:
            print("  -", p)
        return 1
    print(f"coverage_gate：整体 {float(cov['totals']['percent_covered']):.1f}%，高门槛包 ≥90%，达标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
