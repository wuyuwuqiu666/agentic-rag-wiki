"""GraphPort plain 档：顺序执行步骤列表（每个 step 为 `state -> state` 可调用）。

ADR-001：断点续传不依赖编排框架，plain 足够跑通 M0；换 langgraph 只换本实现
（orchestration_langgraph.py，SDK 黑名单对 langgraph 的唯一豁免位）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast


class PlainOrchestration:
    def run(self, steps: Sequence[object], state: object) -> object:
        for step in steps:
            fn = cast("Callable[[object], object]", step)
            state = fn(state)
        return state

    def run_while(
        self,
        steps: list[Callable[[object], object]],
        state: object,
        stop: Callable[[object], bool],
        max_steps: int,
    ) -> object:
        """plain 档条件边：至少跑一跳，随后 stop 或达步数预算即收敛（映射 langgraph 回路+recursion_limit）。"""
        state = self.run(steps, state)  # 首跳必执行
        steps_done = 1
        while not stop(state) and steps_done < max_steps:
            state = self.run(steps, state)
            steps_done += 1
        return state
