"""FR-CAP-2 读侧**降级阶梯**判定内核（④→③→② 逐级降：容量压力下先弃最贵的一层，用户拿"次好答案+说明"而非报错）。

执法点：问答链在检索/生成**之前**取一档，档位由调用方给的**真实容量信号**决定（组合根接 `PoolLedger` 的
在飞总数对 B2 soft(峰水位)/hard 判档——与 `b2.yaml` 同一口径、不另立阈值表），本内核把档翻成读侧可执行上限：
- **0 正常**：多跳预算与生成全开。
- **1 弃④**：`max_hops=1`——agent 多跳是单价最贵的一层（每跳一次检索+一次生成），先停。
- **2 弃④③**：**不发起 LLM 生成**，只把已编译的 ②层 wiki 权威页正文作次好答案（零生成零外呼）；
  无 wiki 页可给则如实拒答并附说明（仍是可呈现的 `Answer`，不是异常/超时）。

立场：阶梯**只减功能、不加失败**——绝不把容量问题变成用户可见报错（FR-CAP-2 的本义）；
也绝不为"看起来降了"而伪造档位来源或静默吞掉降级（降级事实经既有 `breaker_trip` kind 记 `action=alarm`，
不新增 E3 冻结枚举成员）。

诚实边界：`slo.DegradePlan` 的**错误预算燃尽**判级（含第 3 级"全平台静默"）与其逐日作业属另一路信号，
须真实流量（AGENTS.md §8 放行项⑬）；本模块只执法 in-flight 侧的 0/1/2 三档执行半环。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.platform.types import Chunk

# in-flight 阶梯的最高档（第 3 级属 SLO 燃尽路，见模块头诚实边界）
MAX_INFLIGHT_LEVEL = 2
# 次好答案的前缀：用户可见地知道"这是降级件"，不是静默替换
DEGRADE_NOTICE = "【高负载降级】"


@dataclass(frozen=True)
class ReadCaps:
    """一档读侧上限（问答链唯一消费的产物）。"""

    level: int
    max_hops: int
    allow_generation: bool
    reason: str


def ladder_level(count: int, *, soft: int, hard: int) -> int:
    """在飞水位→档：`<soft` 正常、`[soft,hard)` 弃④、`>=hard` 弃④③（B2 仍只 alarm 不 block）。"""
    if count >= hard:
        return 2
    if count >= soft:
        return 1
    return 0


def caps_for(level: int, *, agent_max_hops: int) -> ReadCaps:
    """档→读侧上限；未知/更高档一律钳到本阶梯最高执法档（宁可多降一层，不静默当正常）。"""
    if level >= MAX_INFLIGHT_LEVEL:
        return ReadCaps(
            level=MAX_INFLIGHT_LEVEL,
            max_hops=1,
            allow_generation=False,
            reason=f"FR-CAP-2 弃④③（档 {MAX_INFLIGHT_LEVEL}）：停多跳与 LLM 生成，只给已编译 wiki 权威页的次好答案",
        )
    if level == 1:
        return ReadCaps(
            level=1, max_hops=1, allow_generation=True, reason="FR-CAP-2 弃④（档 1）：停 agent 多跳，单跳检索与生成照常"
        )
    return ReadCaps(
        level=0, max_hops=max(1, agent_max_hops), allow_generation=True, reason="容量正常：消费漏斗四层全开"
    )


def fallback_page(chunks: list[Chunk]) -> Chunk | None:
    """②层既有答案位：取排序结果里第一个 wiki 权威页块；无则 None（由调用方如实拒答并说明）。"""
    for chunk in chunks:
        if chunk.authority == "wiki":
            return chunk
    return None
