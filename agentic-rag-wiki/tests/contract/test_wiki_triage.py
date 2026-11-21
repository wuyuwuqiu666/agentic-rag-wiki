"""M3-a：FR-WIKI-4 triage 判定器单测（三类入选信号 + 降级，纯规则可回放）。

零外部依赖：断言的是判定口径正确性（信号→入选/降级），非模型质量。
"""

from __future__ import annotations

import pytest
from src.partitions.wiki._types import TriageSignals
from src.partitions.wiki.service import triage
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.types import Document

_SHORT_OTHER = "# 便签\n\n一段很短的、不属于任何制度规范、也未达长文门槛的文字。"


@pytest.mark.parametrize(
    ("signals", "expect_compile"),
    [
        (TriageSignals(admin_pinned=True), True),  # 圈定优先，最高
        (TriageSignals(doc_type="policy"), True),  # 静态属性
        (TriageSignals(doc_type="runbook"), True),
        (TriageSignals(doc_type="meeting"), False),  # 纪要不沉淀
        (TriageSignals(doc_type="meeting", admin_pinned=True), True),  # 圈定覆盖纪要排除
        (TriageSignals(doc_type="other", low_confidence_hits=1), True),  # 行为：agent 低置信
        (TriageSignals(doc_type="other", query_freq=5), True),  # 行为：域内高频
        (TriageSignals(doc_type="other", query_freq=4), False),  # 未达高频阈
        (TriageSignals(doc_type="other", token_count=200), True),  # 长文兜底
        (TriageSignals(doc_type="other", token_count=199), False),  # 全未达门槛
    ],
)
def test_should_compile_signals(signals: TriageSignals, expect_compile: bool) -> None:
    selected, reason = triage.should_compile(signals)
    assert selected is expect_compile
    assert bool(reason)  # 任何判定都留可回放依据


@pytest.mark.parametrize(
    ("signals", "expect_down"),
    [
        (TriageSignals(source_major_change=True), True),  # 源大改→退草稿
        (TriageSignals(recent_hit_delta=-3), True),  # 命中走低→退草稿
        (TriageSignals(recent_hit_delta=-2), False),  # 未达走低阈
        (TriageSignals(), False),  # 无降级信号
    ],
)
def test_should_downgrade_signals(signals: TriageSignals, expect_down: bool) -> None:
    down, _ = triage.should_downgrade(signals)
    assert down is expect_down


def test_reason_is_replayable_and_specific() -> None:
    # 同一信号复跑→同依据（可回放性）
    sig = TriageSignals(doc_type="policy")
    assert triage.should_compile(sig) == triage.should_compile(sig)
    assert "policy" in triage.should_compile(sig)[1]


def test_run_once_static_signal_from_event() -> None:
    # 默认无 signal_provider：doc_type 随信封带入，policy→入选建草稿（修旧接线断点）
    container = build_container(Settings())
    container.ingest.process_document(
        Document(doc_id="p1", domain="hr", doc_type="policy", acl_tags=["hr"], raw_text=_SHORT_OTHER)
    )
    assert container.wiki.run_once() >= 1
    assert container.wiki.drafts[-1].triage_reason


def test_run_once_uses_injected_behavior_signals() -> None:
    # 演练：合成行为信号让"其它"短文也入选（FR-MEM-6 学习回路以合成信号演练）
    container = build_container(Settings())
    container.ingest.process_document(
        Document(doc_id="o1", domain="hr", doc_type="other", acl_tags=["hr"], raw_text=_SHORT_OTHER)
    )
    assert container.wiki.run_once() == 0  # 静态信号不足以入选

    container.wiki._signal_provider = lambda ev: TriageSignals(  # type: ignore[attr-defined, assignment]
        doc_type=ev.doc_type, token_count=ev.token_count, low_confidence_hits=2
    )
    container.ingest.process_document(
        Document(doc_id="o2", domain="hr", doc_type="other", acl_tags=["hr"], raw_text=_SHORT_OTHER)
    )
    assert container.wiki.run_once() >= 1  # 行为信号触发编译
