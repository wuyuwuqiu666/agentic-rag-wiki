"""M4 放行项⑮收口：plain↔langgraph 双实现同 `GraphPort` 契约测试套件（G5/E7：换档只换实现、不动业务链）。

核心不变式：对同一 `steps/state/stop/max_steps`，`PlainOrchestration` 与 `LangGraphOrchestration` 必须产出
逐字节相同的结果、并以相同次数、相同顺序执行各 step（编排框架只承载控制流，不改语义）。第二档另证 checkpoint 持久化真实接线。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from src.platform.providers.orchestration_langgraph import LangGraphOrchestration
from src.platform.providers.orchestration_plain import PlainOrchestration


class Box:
    """不透明可变业务态（模拟 `QaState`：各 step 就地改写并原样返回同一对象）。"""

    def __init__(self, n: int = 0) -> None:
        self.n = n
        self.log: list[str] = []

    def add(self, tag: str, delta: int = 1) -> Box:
        self.n += delta
        self.log.append(tag)
        return self

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Box) and (self.n, self.log) == (other.n, other.log)


def _backends() -> list[tuple[str, Any]]:
    return [("plain", PlainOrchestration()), ("langgraph", LangGraphOrchestration())]


@pytest.mark.parametrize("name,graph", _backends())
def test_run_is_identity_on_empty_steps(name: str, graph: Any) -> None:
    b = Box(7)
    assert graph.run([], b) is b
    assert b == Box(7)


@pytest.mark.parametrize("name,graph", _backends())
def test_run_applies_steps_in_order_once(name: str, graph: Any) -> None:
    steps = [lambda s: s.add("a"), lambda s: s.add("b", 10), lambda s: s.add("c")]
    out = graph.run(steps, Box(0))
    assert (out.n, out.log) == (12, ["a", "b", "c"])


@pytest.mark.parametrize("name,graph", _backends())
def test_run_over_non_mutating_state(name: str, graph: Any) -> None:
    steps: list[Callable[[object], object]] = [lambda x: x + 1, lambda x: x * 10]
    assert graph.run(steps, 3) == 40


def test_run_backends_produce_identical_results() -> None:
    steps = [lambda s: s.add("a"), lambda s: s.add("b"), lambda s: s.add("c")]
    plain = PlainOrchestration().run(steps, Box(0))
    lg = LangGraphOrchestration().run(steps, Box(0))
    assert plain == lg


def _runs_per_pass(steps: list[Callable[[object], object]], stop: Callable[[object], bool], max_steps: int):
    """构造同一 scenario，分别用两档跑，返回各自最终态供逐字节比较。"""
    return (
        PlainOrchestration().run_while(steps, Box(0), stop, max_steps),
        LangGraphOrchestration().run_while(steps, Box(0), stop, max_steps),
    )


@pytest.mark.parametrize(
    "max_steps,expect_n",
    [
        (0, 1),  # 首跳无条件执行（即便预算 0）
        (1, 1),  # 预算 1 = 仅首跳
        (2, 2),  # 每跳 n+1，续到预算耗尽
        (5, 5),
    ],
)
def test_run_while_never_stop_is_bounded_by_max_steps(max_steps: int, expect_n: int) -> None:
    steps = [lambda s: s.add("x")]
    plain, lg = _runs_per_pass(steps, lambda s: False, max_steps)
    assert plain == lg
    assert (plain.n, lg.n) == (expect_n, expect_n)
    assert len(plain.log) == len(lg.log) == expect_n  # 每档执行相同跳数


def test_run_while_stop_immediately_runs_first_pass_once() -> None:
    steps = [lambda s: s.add("x")]
    plain, lg = _runs_per_pass(steps, lambda s: True, 5)
    assert plain == lg
    assert plain.n == lg.n == 1  # stop 恒真仍完成首跳（映射 plain"首跳必执行"）


def test_run_while_converges_when_stop_turns_true() -> None:
    steps = [lambda s: s.add("x")]
    # n>=3 即停：首跳 n=1，续至 n=3 停 → 共 3 跳（不越 max_steps=10）
    plain, lg = _runs_per_pass(steps, lambda s: s.n >= 3, 10)
    assert plain == lg
    assert plain.n == lg.n == 3


def test_run_while_multi_step_cycle_order_and_passes_match() -> None:
    # 一圈含三节点，各自打标；stop 依跳数（每跳 recall 节点 +1）
    def recall(s: Box) -> Box:
        return s.add("recall")

    def rank(s: Box) -> Box:
        s.log.append("rank")
        return s

    def advance(s: Box) -> Box:
        s.log.append("advance")
        return s

    steps = [recall, rank, advance]
    def stop(s: Box) -> bool:
        return s.n >= 2

    plain, lg = _runs_per_pass(steps, stop, 8)
    assert plain == lg
    assert plain.n == lg.n == 2
    assert plain.log == ["recall", "rank", "advance", "recall", "rank", "advance"]


def test_run_while_empty_steps_is_identity() -> None:
    plain, lg = _runs_per_pass([], lambda s: False, 3)
    assert plain == lg == Box(0)


def test_checkpoint_persistence_is_wired_and_matches_plain() -> None:
    """with_memory_checkpoint：run 终态落检查点、可读回，且业务结果仍与 plain 逐字节相同。

    检查点态须可序列化（msgpack），故此处用 dict 态演示持久化管线；不可序列化对象（如需 pickle）属企业档放行。
    """
    steps: list[Callable[[object], object]] = [
        lambda d: {**d, "n": d["n"] + 1},
        lambda d: {**d, "tag": d["tag"] + "b"},
    ]
    orch = LangGraphOrchestration.with_memory_checkpoint(thread_id="t1")
    assert orch.read_checkpoint() is None  # 未跑=无检查点
    out = orch.run(steps, {"n": 0, "tag": ""})
    assert out == PlainOrchestration().run(steps, {"n": 0, "tag": ""})  # 带检查点不改业务链
    snap = orch.read_checkpoint()
    assert snap is not None and snap["payload"] == out
