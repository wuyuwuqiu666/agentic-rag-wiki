"""金标 harness 自检（§9 产物 #7 / FR-MEM-5）。

CI 只验**评测机械链路正确**（schema 校验、只读 loader 坏行 fail-loud、scorer 指标算式）——
不在此断言幻觉率/引用正确率达标（真实 200 题人工金标=放行前置项；dev 合成档非质量基线，AGENTS §8 诚实账）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.platform.types import Answer, Citation

from tests.eval import run as eval_run
from tests.eval.golden import GoldenQuestion, load_golden, score

_FIXTURE = Path(__file__).parent / "_fixtures" / "synthetic_golden.jsonl"


def _ans(question: str, cited: list[str], *, refused: bool = False) -> Answer:
    return Answer(
        question=question,
        refused=refused,
        citations=[Citation(chunk_id=c, doc_id=c.split("#")[0], doc_version=1) for c in cited],
    )


def test_loader_reads_all_four_categories_and_validates() -> None:
    items = load_golden(_FIXTURE)
    assert len(items) == 5  # 4 类各 1 + 1 条 B4 注入探测（复用制度类）
    assert {q.category for q in items} == {"制度", "合同", "纪要", "代码wiki"}
    injection_cases = [q for q in items if q.injection]
    assert len(injection_cases) == 1 and injection_cases[0].expected_refusal is True  # 注入题正确行为=拒答


def test_loader_fails_loud_on_bad_line(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"question": "ok", "category": "制度"}\n{"question": "缺类别"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=":2"):
        load_golden(bad)


def test_scorer_math_on_crafted_answers() -> None:
    gold = [
        GoldenQuestion(question="q1", gold_chunk_ids=["a#0", "b#0"], category="制度"),
        GoldenQuestion(question="q2", gold_chunk_ids=["c#0"], category="合同"),
        GoldenQuestion(question="q3", gold_chunk_ids=[], category="纪要", expected_refusal=True),
    ]
    pairs = [
        (_ans("q1", ["a#0", "x#9"]), gold[0]),  # 命中 a#0，幻觉引用 x#9；b#0 未被引（召回缺口）
        (_ans("q2", ["c#0"]), gold[1]),  # 全对
        (_ans("q3", [], refused=True), gold[2]),  # 期望拒答且实际拒答 → refusal_ok
    ]
    m = score(pairs)
    # 引用集：q1={a,x}(2)、q2={c}(1)、q3={} → cited=3；命中 gold：a + c =2；幻觉：x=1；gold 总数=2+1+0=3
    assert m.total == 3 and m.answered == 2 and m.refused == 1
    assert m.citation_precision == pytest.approx(2 / 3)
    assert m.hallucination_rate == pytest.approx(1 / 3)
    assert m.recall == pytest.approx(2 / 3)
    assert m.refusal_accuracy == pytest.approx(3 / 3)  # 三题期望/实际拒答全一致


def test_scorer_injection_resistance_is_its_own_dimension() -> None:
    """§9#7 B4 注入回归独立口径：分母=标记 injection 的用例，分子=其中正确抵抗（拒答）者；与通用拒答率解耦。"""
    gold = [
        GoldenQuestion(question="inj1", category="制度", injection=True),
        GoldenQuestion(question="inj2", category="制度", injection=True),
        GoldenQuestion(question="normal", category="合同", gold_chunk_ids=["a#0"]),  # 非注入题不计入注入分母
    ]
    pairs = [
        (_ans("inj1", [], refused=True), gold[0]),  # 抵抗成功
        (_ans("inj2", ["a#0"], refused=False), gold[1]),  # 被劫持（未拒答）=抵抗失败
        (_ans("normal", ["a#0"]), gold[2]),
    ]
    m = score(pairs)
    assert m.injection_cases == 2
    assert m.injection_resistance == pytest.approx(1 / 2)  # 2 注入例抵抗中 1
    # 与通用拒答率独立：normal 题不应答拒答、两注入题行为各异 → refusal_accuracy 另算，不左右注入维度
    assert m.refusal_accuracy == pytest.approx(2 / 3)


def test_scorer_injection_resistance_zero_when_no_injection_cases() -> None:
    """无注入用例=分母 0，injection_resistance 归 0 不抛（占位集若无注入题也不崩）。"""
    gold = [GoldenQuestion(question="q", category="制度", gold_chunk_ids=["a#0"])]
    m = score([(_ans("q", ["a#0"]), gold[0])])
    assert m.injection_cases == 0 and m.injection_resistance == 0.0


def test_run_suite_hallucination_writes_non_quality_report(tmp_path: Path) -> None:
    """§11 文档命令 `--suite hallucination` 可跑通（机械链路），dev 合成档报告显式非质量结论。"""
    m = eval_run.run(out_dir=tmp_path)  # 缺省 suite=hallucination + 合成占位
    assert m.total == 5  # 合成集 5 题（含 1 条 B4 注入探测；不断言任何质量率达标）
    assert m.injection_cases == 1  # 注入维度在跑分里真实存在
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "suite=hallucination" in report
    assert "B4 注入回归" in report  # §9#7 三维之一的独立口径行须渲染
    assert "非质量结论" in report  # 诚实账：占位数字不得被引用为达标口径


def test_run_unknown_suite_fails_loud() -> None:
    with pytest.raises(ValueError, match="未知评测套件"):
        eval_run.run(suite="not-a-suite")


def test_main_accepts_suite_flag() -> None:
    assert eval_run.main(["--suite", "hallucination"]) == 0  # argparse 接受文档化短参，不报 unrecognized
