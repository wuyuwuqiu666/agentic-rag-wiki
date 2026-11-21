"""金标评测跑分入口（§11 自验证循环 / G3）。尺子只读，不产在线反馈回路。

`python -m tests.eval.run --suite hallucination`（§9 产物 #7 幻觉套件，缺省合成占位集，仅演示机械链路）
`python -m tests.eval.run --golden workspace/golden/qa.jsonl`（人工核定 200 题就位后跑真实评测）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.platform.config import Settings
from src.platform.gateway import build_container

from tests.eval.golden import EvalMetrics, GoldenQuestion, load_golden, score

_SYNTHETIC = Path(__file__).parent / "_fixtures" / "synthetic_golden.jsonl"
# §9 产物 #7 幻觉套件=引用正确率 + 拒答正确率 + B4 注入回归（三维各有独立口径；注入回归=`GoldenQuestion.injection`
# 标记用例上"正确抵抗=拒答"的占比，由 `score()` 的 `injection_resistance` 独立计，不复用通用 expected_refusal）。
SUITES = ("hallucination",)


def _resolve_golden(path: str | None) -> tuple[list[GoldenQuestion], bool]:
    """返回 (题目, 是否人工金标)。人工金标=放行前置项；缺省回落合成占位（非质量结论）。"""
    if path:
        return load_golden(Path(path)), True
    return load_golden(_SYNTHETIC), False


def run(
    golden_path: str | None = None,
    *,
    suite: str = "hallucination",
    out_dir: Path | None = None,
) -> EvalMetrics:
    if suite not in SUITES:
        raise ValueError(f"未知评测套件 {suite!r}，可选：{list(SUITES)}")
    questions, is_human = _resolve_golden(golden_path)
    container = build_container(Settings())
    pairs = [(container.app.answer(q.question, user_acl=[q.domain]), q) for q in questions]
    metrics = score(pairs)

    out = out_dir or Path("workspace/eval")
    out.mkdir(parents=True, exist_ok=True)
    note = "人工核定金标（可作 G3 质量口径）" if is_human else "合成占位金标（非质量结论，仅验机械链路）"
    lines = [
        f"# 评测报告 — suite={suite} · {note}",
        "",
        f"- 题数：{metrics.total}（应答 {metrics.answered} / 拒答 {metrics.refused}）",
        f"- 引用正确率：{metrics.citation_precision:.3f}",
        f"- 幻觉率：{metrics.hallucination_rate:.3f}",
        f"- 召回覆盖：{metrics.recall:.3f}",
        f"- 拒答正确率：{metrics.refusal_accuracy:.3f}",
    ]
    if metrics.injection_cases:
        lines.append(
            f"- B4 注入回归（{metrics.injection_cases} 例）：{metrics.injection_resistance:.3f}"
        )
    if not is_human:
        lines.append("")
        lines.append("> 数字来自 dev 合成档占位集，**不得引用为幻觉率达标结论**（AGENTS §8 诚实账 / FR-MEM-5）。")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"eval suite={suite}：{note} — 报告落 {out / 'report.md'}")
    print("\n".join(lines[2:]))
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agentic RAG 金标评测跑分")
    parser.add_argument("--golden", default=None, help="人工核定金标 JSONL 路径；缺省=合成占位集")
    parser.add_argument("--suite", default="hallucination", choices=SUITES, help="评测套件（§9 #7）")
    args = parser.parse_args(argv)
    run(args.golden, suite=args.suite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
