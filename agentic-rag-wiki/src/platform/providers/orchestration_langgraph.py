"""GraphPort 企业档：LangGraph 编排（ADR-001 双实现之第二档）。需 `pip install langgraph`（MIT）方可实例化。

ADR-001：`orchestration_plain.py`（自研 DAG）为单机执行档主实现；本档为条件边/interrupt/checkpoint 的
企业档实现，同 `GraphPort` 契约、由装配表 `c.orchestration` 切换（G5"换档只改本表"）。

行为等价不变式（G5/E7·放行项⑮ 收口）：`run` 与 `run_while` 必须与 plain 档对同一 `steps/state/stop/max_steps`
产出逐字节相同的结果——编排框架只承载控制流（StateGraph 节点 + 条件边 + recursion_limit），不改业务链。
`run_while` 精确复刻 plain 语义：首跳必执行，随后 `not stop(state) and passes < max_steps` 才续跑整段 steps。

checkpoint 持久化=可选注入（默认 None→热路径不序列化任意 pydantic 态、零 msgpack 依赖）；
`with_memory_checkpoint()` 提供内存检查点工厂，供人审中断/断点续传语义演练（`interrupt` 真·跨进程持久化=企业档放行）。
langgraph/langchain 一律只 import 于本文件（§6 禁令3，check_imports 机检，M4-bk）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict, cast
from uuid import uuid4

from src.platform.providers.errors import ProviderNotConfigured


class _Channel(TypedDict):
    """LangGraph 状态通道：承载不透明业务态（如 `QaState`）+ 回路 pass 计数。

    业务态在 plain 档是被各 step 就地改写并原样透传的对象，这里用 `payload` 单通道保同一对象引用，
    `passes` 记录已完成回路轮数以复刻 plain 的 `steps_done` 判据。
    """

    payload: object
    passes: int


@dataclass(frozen=True)
class _LangGraphApi:
    StateGraph: Any
    START: str
    END: str


def _require_langgraph() -> _LangGraphApi:
    """缺驱动即 fail-loud（绝不静默回落 plain）；成功则返回本档所需 API 句柄。"""
    try:
        from langgraph.graph import END, START, StateGraph  # noqa: PLC0415

        return _LangGraphApi(StateGraph=StateGraph, START=START, END=END)
    except ImportError as exc:  # pragma: no cover - 取决于宿主是否装 langgraph
        raise ProviderNotConfigured(
            "langgraph 编排档需 pip install langgraph（T4 新依赖，MIT；放行项⑮ 已备真实现，缺驱动即 fail-loud 不回落 plain）"
        ) from exc


class LangGraphOrchestration:
    """`GraphPort` 的 LangGraph 实现：把 `run`/`run_while` 映射为 StateGraph 节点图 + 条件边回路。"""

    def __init__(self, *, checkpointer: object | None = None, thread_id: str | None = None) -> None:
        self._api = _require_langgraph()
        self._checkpointer = checkpointer
        self._thread_id = thread_id
        self._last_app: object | None = None
        self._last_config: dict[str, object] | None = None

    @classmethod
    def with_memory_checkpoint(cls, *, thread_id: str | None = None) -> LangGraphOrchestration:
        """构造带内存检查点的实例（演示 checkpoint 持久化 / 人审中断语义，langgraph import 仍只在本文件）。

        给定 `thread_id` 时，多次 `run` 复用同一检查点线程、可据 `read_checkpoint()` 读回上次落盘态。
        """
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        return cls(checkpointer=MemorySaver(), thread_id=thread_id or "lg-default")

    def read_checkpoint(self) -> dict[str, object] | None:
        """读回最近一次 run 的检查点终态（未配置 checkpointer=返回 None）。"""
        if self._checkpointer is None or self._last_app is None or self._last_config is None:
            return None
        snapshot = cast("Any", self._last_app).get_state(self._last_config)
        return cast("dict[str, object]", snapshot.values)

    def _run_graph(self, app: object, state: object, *, supersteps: int) -> object:
        config: dict[str, object] = {"recursion_limit": max(supersteps, 1) + 4}
        if self._checkpointer is not None:
            config["configurable"] = {"thread_id": self._thread_id or uuid4().hex}
            self._last_app = app
            self._last_config = config
        out = cast("Any", app).invoke({"payload": state, "passes": 0}, config=config)
        return out["payload"]

    def run(self, steps: Sequence[object], state: object) -> object:
        """线性 DAG：逐 step 建节点、START→…→END 顺序透传 payload（空 steps=恒等，等价 plain）。"""
        if len(steps) == 0:
            return state
        api = self._api

        def mk(step: object) -> Callable[[_Channel], dict[str, object]]:
            def node(chan: _Channel) -> dict[str, object]:
                fn = cast("Callable[[object], object]", step)
                return {"payload": fn(chan["payload"])}

            return node

        graph = api.StateGraph(_Channel)
        prev = api.START
        for i, step in enumerate(steps):
            name = f"step{i}"
            graph.add_node(name, mk(step))
            graph.add_edge(prev, name)
            prev = name
        graph.add_edge(prev, api.END)
        app = graph.compile(checkpointer=self._checkpointer)
        return self._run_graph(app, state, supersteps=len(steps))

    def run_while(
        self,
        steps: list[Callable[[object], object]],
        state: object,
        stop: Callable[[object], bool],
        max_steps: int,
    ) -> object:
        """条件边回路：steps 串成一圈 + tick 计数节点，`tick` 后条件边据 `not stop and passes<max_steps` 决定续跑或 END。

        首圈无条件执行（映射 plain"首跳必执行"）；空 steps=恒等返回（每圈皆空跑，与 plain 同果）。
        """
        if len(steps) == 0:
            return state
        api = self._api

        def mk(step: Callable[[object], object]) -> Callable[[_Channel], dict[str, object]]:
            def node(chan: _Channel) -> dict[str, object]:
                return {"payload": step(chan["payload"])}

            return node

        def tick(chan: _Channel) -> dict[str, int]:
            return {"passes": chan["passes"] + 1}

        def route(chan: _Channel) -> str:
            if not stop(chan["payload"]) and chan["passes"] < max_steps:
                return "loop"
            return "done"

        graph = api.StateGraph(_Channel)
        first = "step0"
        for i, step in enumerate(steps):
            graph.add_node(f"step{i}", mk(step))
        graph.add_node("tick", tick)
        prev = api.START
        for i in range(len(steps)):
            graph.add_edge(prev, f"step{i}")
            prev = f"step{i}"
        graph.add_edge(prev, "tick")
        graph.add_conditional_edges("tick", route, {"loop": first, "done": api.END})
        app = graph.compile(checkpointer=self._checkpointer)
        return self._run_graph(app, state, supersteps=max_steps * (len(steps) + 1))
