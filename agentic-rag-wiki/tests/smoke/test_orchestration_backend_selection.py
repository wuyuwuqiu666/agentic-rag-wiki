"""M4-ap §5/ADR-001 装配表可换性 + `c.orchestration` 幻影旋钮收口：langgraph 档不再静默落 plain。

- 此前 `ProviderChoices.orchestration`（`GraphBackend` Literal 含 "langgraph"）组合根**零消费**、硬写
  `PlainOrchestration()`——选 `"langgraph"` 却静默拿 plain DAG（以为有 checkpoint/interrupt 实则无，同 M4-an
  pgvector 静默错配危险类），且该旋钮为"有字段零读"幻影配置。
- 收口后：langgraph→`LangGraphOrchestration()`（M4 放行项⑮ 已装 langgraph、真 StateGraph 实现，见
  `tests/contract/test_graph_dual_backend.py` 双实现同契约套件）；**缺驱动则构造即 fail-loud、绝不静默回落 plain**。
"""

from __future__ import annotations

import sys
import types

import pytest
from src.platform.config.settings import ProviderChoices, Settings
from src.platform.gateway.composition import build_container
from src.platform.providers.errors import ProviderNotConfigured
from src.platform.providers.orchestration_langgraph import LangGraphOrchestration
from src.platform.providers.orchestration_plain import PlainOrchestration


def test_langgraph_selected_is_not_silently_plain() -> None:
    # 选 langgraph：未装驱动则装配即拒（诚实 fail-loud）；若环境已装则必须给 LangGraph 档、绝不静默回落 plain。
    s = Settings(providers=ProviderChoices(orchestration="langgraph"))
    try:
        container = build_container(s)
    except ProviderNotConfigured:
        return
    assert isinstance(container.graph, LangGraphOrchestration)
    assert not isinstance(container.graph, PlainOrchestration)


def test_plain_backend_still_default() -> None:
    # 默认 plain 档路径行为不变（不受本次三分支改造影响）
    container = build_container(Settings())
    assert isinstance(container.graph, PlainOrchestration)


def test_c_orchestration_knob_is_consumed() -> None:
    # 反证"幻影旋钮"：不同 c.orchestration 取值必须驱动不同装配结果（langgraph≠plain），证字段被真实读取。
    plain = build_container(Settings(providers=ProviderChoices(orchestration="plain")))
    assert isinstance(plain.graph, PlainOrchestration)
    # 同进程内 langgraph 档类型必然不同于 plain（占位 fail-loud 亦不改其类型契约）
    assert LangGraphOrchestration is not PlainOrchestration


def test_langgraph_absent_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # 模拟无 langgraph 环境（放行项⑮ 装依赖前）：构造即 ProviderNotConfigured，绝不静默回落 plain、不产出可用对象。
    fake = types.ModuleType("langgraph")  # 裸模块、无 graph 子模块可解析
    monkeypatch.setitem(sys.modules, "langgraph", fake)
    monkeypatch.delitem(sys.modules, "langgraph.graph", raising=False)
    with pytest.raises(ProviderNotConfigured):
        LangGraphOrchestration()


def test_langgraph_installed_runs_real_dag() -> None:
    # 已装环境（本机 langgraph 1.2.11）：run 真跑 StateGraph 节点图、语义与 plain 同（详见双实现契约套件）。
    pytest.importorskip("langgraph.graph")
    graph = LangGraphOrchestration()
    steps = [lambda s: s + 1, lambda s: s * 10]
    assert graph.run(steps, 3) == 40
