"""M3-d：FR-MEM-3/4/6 学习回路提案人审最小闭环（合成信号演练，单机不自动应用）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from src.partitions.memory._types import SignalSnapshot
from src.partitions.memory.runtime.loop import LearningLoop
from src.partitions.memory.service import report

_EV = ["audit:q-001", "audit:q-002"]


def test_cold_start_no_proposals(tmp_path) -> None:
    loop = LearningLoop(outdir=str(tmp_path))
    snap = SignalSnapshot(sample_size=50, refusal_rate=0.9, low_conf_rate=0.9, evidence=_EV)
    assert loop.run(snap) == []  # 小样本=噪声，禁调参（FR-MEM-6）


def test_abnormal_refusal_yields_safety_proposal_only(tmp_path) -> None:
    loop = LearningLoop(outdir=str(tmp_path))
    snap = SignalSnapshot(sample_size=500, refusal_rate=0.7, low_conf_rate=0.1, evidence=_EV)
    props = loop.run(snap)
    assert [p.param for p in props] == ["reject_threshold"]
    safety = props[0]
    assert safety.param_class == "safety"
    assert safety.auto_applicable is False  # 安全参数恒不可自动生效
    assert safety.status == "proposed"  # 演练期停于提案


def test_low_conf_yields_business_proposal(tmp_path) -> None:
    loop = LearningLoop(outdir=str(tmp_path))
    snap = SignalSnapshot(sample_size=500, refusal_rate=0.1, low_conf_rate=0.4, evidence=_EV)
    props = loop.run(snap)
    assert [p.param for p in props] == ["answer_topn"]
    biz = props[0]
    assert biz.param_class == "business"
    assert biz.auto_applicable is False  # 单机期一律不自动应用（即便 business）
    assert biz.status == "proposed"


def test_healthy_signals_yield_nothing(tmp_path) -> None:
    loop = LearningLoop(outdir=str(tmp_path))
    snap = SignalSnapshot(sample_size=500, refusal_rate=0.1, low_conf_rate=0.1, evidence=_EV)
    assert loop.run(snap) == []


def test_proposal_reports_are_format_compliant(tmp_path) -> None:
    loop = LearningLoop(outdir=str(tmp_path))
    props = loop.run(SignalSnapshot(sample_size=500, refusal_rate=0.7, low_conf_rate=0.5, evidence=_EV))
    assert props
    for prop in props:
        assert prop.shadow_report  # 报告落文件并回填路径
        text = (tmp_path / f"{prop.param}.md").read_text(encoding="utf-8")
        assert report.is_compliant(text)  # 七段齐全 + 依据样本非空


def test_safety_never_auto_applicable_is_type_invariant() -> None:
    # 跨区三禁③的类型层护栏：构造 safety+auto_applicable=True 直接违反不变式
    from src.platform.types import ParamProposal

    with pytest.raises(ValidationError):
        ParamProposal(param="reject_threshold", param_class="safety", auto_applicable=True)


def test_loop_never_emits_applied_status(tmp_path) -> None:
    loop = LearningLoop(outdir=str(tmp_path))
    props = loop.run(SignalSnapshot(sample_size=999, refusal_rate=0.8, low_conf_rate=0.6, evidence=_EV))
    assert all(p.status not in {"approved", "applied"} for p in props)  # FR-MEM-6 单机期不自动应用任何参数
