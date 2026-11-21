"""MqPort 执行档：进程内队列，语义对齐生产 PG `SKIP LOCKED` 租约（v0.7 缩量：不引 Kafka；FR-CAP-4 单机档 MQ 吸收写洪峰）。

canonical 事件流 / 变更流走此端口；publish 入 topic，drain **取得**（入租、不确认），ack 才真正消费（ADR-011）。
增量流优先级由消费侧按 topic 决定。

租约这一半不是讲究：本档原先是"drain 即消费"，消费侧批量循环中途抛错时，已弹出的剩余事件既没落地
也不在积压里＝静默消失。现按 `SKIP LOCKED` 的读法建模——取走的条目记在 `_leased`，未确认的部分
① 退回队首（保持相对次序，重投不改变事件先后）、② 仍计入 `depth`（"卡住"必须看得见）。
"""

from __future__ import annotations

from collections import defaultdict


class PgQueueMq:
    def __init__(self) -> None:
        self._topics: dict[str, list[dict[str, object]]] = defaultdict(list)
        # 在租＝已 drain 未取得、尚未 ack 的条目（按取得顺序），对应 PG 的行锁 / Kafka 的未提交 offset。
        self._leased: dict[str, list[dict[str, object]]] = defaultdict(list)

    def publish(self, topic: str, payload: dict[str, object]) -> None:
        self._topics[topic].append(payload)

    def drain(self, topic: str, max_items: int) -> list[dict[str, object]]:
        q = self._topics[topic]
        out, self._topics[topic] = q[:max_items], q[max_items:]
        self._leased[topic].extend(out)
        return out

    def ack(self, topic: str, count: int) -> int:
        """确认在租条目的前 `count` 条已消费，其余退回队首；返回退回条数。

        越界（负数或大于在租数）＝消费侧记账错乱，直接 `ValueError`：静默夹紧会把"少确认"读成"都消费完了"，
        与本项目"错误不得静默"（NFR-8）同一条红线。
        """
        leased = self._leased[topic]
        if count < 0 or count > len(leased):
            raise ValueError(f"ack 越界：topic={topic!r} 在租 {len(leased)} 条，请求确认 {count} 条（消费侧计数错乱，不静默夹紧）")
        self._leased[topic] = []
        requeue = leased[count:]
        self._topics[topic] = requeue + self._topics[topic]
        return len(requeue)

    def depth(self, topic: str) -> int:
        """积压＝未取用 ＋ 在租未确认：租约未走完的条目仍是待办工作，否则卡住的那批会从指标上消失。"""
        return len(self._topics[topic]) + len(self._leased[topic])
