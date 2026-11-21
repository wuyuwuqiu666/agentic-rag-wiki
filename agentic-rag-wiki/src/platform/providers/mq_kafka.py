"""MqPort 企业档：Kafka（ADR 装配表可换档）。放行项⑯ 真实现（M4-bu）。

G5/ADR-005：`pg_queue` 为执行档默认队列；`kafka` 是企业分布式档，同 `MqPort` 契约、由装配表 `c.mq` 切换。
M4-aq 收口此前 `c.mq` 组合根零读的静默错配；M4-bu 把占位升为**真 confluent-kafka 适配**——`publish` 经
`Producer.produce`+`flush` 落 JSON 信封、`drain` 经 `Consumer.poll` 反序列化回 dict（与 pg_queue 逐 dict 等价语义）。
M4-cp（ADR-011）补上契约的租约半边：`drain` 只**取得**、`ack` 才**确认**——Kafka 的原生映射＝
**不提交 offset 即重投**，故消费端显式 `enable.auto.commit=false`（开着自动提交会让"确认"绕过 `ack` 自己发生，
租约语义当场失效＝与 pg_queue 不再等价）；`ack(topic, count)` 对前 count 条 `commit(message=)`，其余 `seek(message=)`
退回本会话读取位（未提交只保证"重启/rebalance 后重投"，同会话内不 seek 就再也读不到）。
逐条 commit 是正确性优先的取法（跨分区时提交最后一条会漏掉其他分区的进度），吞吐数字＝放行项⑯ 未测、不谎称。

懒导入纪律：组合根**无条件** `from ...mq_kafka import KafkaMq`（顶层），故本模块顶层**不得** import
confluent_kafka——否则未装 extra 的单机默认档连装配 import 都崩。故只在构造 producer/consumer 时函数内 import，
缺驱动/缺 broker 即 `ProviderNotConfigured` fail-loud、绝不静默回落 pg_queue（同 M4-ap/M4-bu s3）。
`producer=`/`consumer=` 注入位供离线契约测试（进程内 fake 跑通 publish→drain→ack 的 JSON 序列化映射与租约映射）——
真实 broker 往返须 broker 供给，属企业放行档，本环不伪造。`depth`（consumer lag）须真实 broker + 共享 committed offset，
默认 fail-loud、经 `lag_provider=` 注入方显（"depth→consumer lag 映射"的端口缝，承 M4-z backlog 语义），不谎称已接分布式 lag。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from src.platform.providers.errors import ProviderNotConfigured


def encode(payload: dict[str, object]) -> bytes:
    """payload dict → JSON 字节信封（sort_keys 令逐字节确定、可与 pg_queue dict 语义对账）。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def decode(raw: bytes | str) -> dict[str, object]:
    obj = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    if not isinstance(obj, dict):
        raise ProviderNotConfigured(f"kafka 信封须为 JSON 对象，实得 {type(obj).__name__}（载荷契约破坏，不静默吞）")
    return {str(k): v for k, v in obj.items()}


def _require_confluent() -> Any:
    try:
        import confluent_kafka  # noqa: PLC0415

        return confluent_kafka
    except ImportError as exc:  # pragma: no cover - 取决于环境是否装 extra
        raise ProviderNotConfigured(
            "kafka 档需 pip install confluent-kafka（放行项⑯ 已备真实现，Apache-2.0；缺驱动即 fail-loud 不静默回落 pg_queue）"
        ) from exc


class KafkaMq:
    def __init__(
        self,
        broker: str | None = None,
        *,
        producer: Any = None,
        consumer: Any = None,
        group_id: str = "agentic-rag-wiki",
        lag_provider: Callable[[str], int] | None = None,
    ) -> None:
        self._broker = broker
        self._lag_provider = lag_provider
        self._subscribed: set[str] = set()
        # 在租＝本轮 drain 已取得、尚未 ack 的消息对象（按取得顺序），对应"未提交 offset"的那批。
        self._leased: dict[str, list[Any]] = {}
        # 注入 client（离线契约测试）则不触懒导入；否则按 broker 真构造（缺 broker/缺驱动均 fail-loud）。
        self._producer = producer if producer is not None else self._new_producer()
        self._consumer = consumer if consumer is not None else self._new_consumer(group_id)

    def _new_producer(self) -> Any:
        if not self._broker:
            raise ProviderNotConfigured("kafka 需 broker 配置（env 注入 kafka_broker，T1/企业放行项）")
        ck = _require_confluent()
        return ck.Producer({"bootstrap.servers": self._broker})

    def _new_consumer(self, group_id: str) -> Any:
        if not self._broker:
            raise ProviderNotConfigured("kafka 需 broker 配置（env 注入 kafka_broker，T1/企业放行项）")
        ck = _require_confluent()
        # enable.auto.commit=false 是租约语义的前提：自动提交会让"确认"绕开 ack 自己发生（ADR-011）。
        return ck.Consumer({"bootstrap.servers": self._broker, "group.id": group_id, "enable.auto.commit": "false"})

    def publish(self, topic: str, payload: dict[str, object]) -> None:
        self._producer.produce(topic, value=encode(payload))
        self._producer.flush()

    def drain(self, topic: str, max_items: int) -> list[dict[str, object]]:
        if topic not in self._subscribed:
            self._consumer.subscribe([topic])
            self._subscribed.add(topic)
        out: list[dict[str, object]] = []
        self._leased.setdefault(topic, [])
        while len(out) < max_items:
            msg = self._consumer.poll(1.0)
            if msg is None:  # poll 超时=暂无更多积压（对齐 pg_queue 排空即返，非错误）
                break
            err = getattr(msg, "error", None)
            if callable(err) and err() is not None:
                raise ProviderNotConfigured(f"kafka 消费错误：{err()}（不静默跳过消息）")
            self._leased[topic].append(msg)  # 先记租约再解码：解码失败的消息同样不得凭空消失
            out.append(decode(msg.value()))
        return out

    def ack(self, topic: str, count: int) -> int:
        """确认前 `count` 条（逐条 `commit(message=)`），其余 `seek(message=)` 退回读取位；返回退回条数。

        越界走 `ValueError`（与 pg_queue 同一口径、同一理由：静默夹紧＝把"少确认"读成"都消费完了"）。
        未确认部分只靠 seek 在同会话内重投；进程/rebalance 后靠"未提交 offset"重投，两条都是至少一次。
        """
        leased = self._leased.get(topic, [])
        if count < 0 or count > len(leased):
            raise ValueError(f"ack 越界：topic={topic!r} 在租 {len(leased)} 条，请求确认 {count} 条（消费侧计数错乱，不静默夹紧）")
        self._leased[topic] = []
        for msg in leased[:count]:
            self._consumer.commit(message=msg, asynchronous=False)
        for msg in leased[count:]:
            self._consumer.seek(msg)
        return len(leased) - count

    def depth(self, topic: str) -> int:
        """积压＝consumer lag（按**已提交 offset** 度量），故在租未 ack 的部分天然计入＝卡住看得见（ADR-011）。"""
        if self._lag_provider is None:
            raise ProviderNotConfigured(
                "kafka depth（consumer lag）映射须真实 broker + 共享 committed offset（企业放行项⑯）；"
                "注入 lag_provider 方显此端口缝，本环不伪造积压数"
            )
        return int(self._lag_provider(topic))
