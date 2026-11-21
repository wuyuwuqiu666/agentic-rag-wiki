"""放行项⑯（M4-bu）：对象库/队列企业档真实现的**双实现同端口契约**套件（G5/E7）。

命题=「换档只换实现、不动业务链」：`filesystem`↔`s3` 同 `ObjectStorePort`、`pg_queue`↔`kafka` 同 `MqPort`，
对同一操作序列须产出**逐观测等值**的结果。离线纪律（不越 T5、不伪造）：
- S3 经 `botocore.stub.Stubber` 拦截在序列化层（真实 boto3 客户端、真实请求形状校验，**无网络、无真实桶**），
  证明适配器把端口方法映射为正确 S3 API 调用；真实桶往返须 endpoint/凭证/服务端=企业放行档、本环不谎称。
- Kafka 经**注入的进程内 fake producer/consumer**（含 librdkafka 之外的全部适配逻辑：JSON 序列化信封、
  subscribe/poll 编排、flush 时机、以及 ADR-011 的 commit/seek 租约位点），证明 publish→drain→ack 的端口语义
  与 pg_queue 等值；真实 broker 往返=企业放行档。
"""

from __future__ import annotations

import io
import sys
from typing import Any

import pytest
from src.platform.providers.errors import ProviderNotConfigured
from src.platform.providers.mq_kafka import KafkaMq, decode, encode
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.objectstore_s3 import S3ObjectStore

_BUCKET = "kb-canonical"


# ---------------- S3：FileSystem ↔ S3(Stubber) 同 ObjectStorePort 契约 ----------------
def _stubbed_s3() -> tuple[S3ObjectStore, Any]:
    import boto3
    from botocore.stub import Stubber

    client = boto3.client(
        "s3",
        aws_access_key_id="ak-test",  # 仅 Stubber 本地签名占位，绝不触网
        aws_secret_access_key="sk-test",
        region_name="us-east-1",
        endpoint_url="http://stubbed.invalid",
    )
    stub = Stubber(client)
    return S3ObjectStore(_BUCKET, client=client), stub


def test_s3_put_get_exists_matches_filesystem_semantics(tmp_path: Any) -> None:
    key, data = "doc-a/1", b"canonical body \x00\xff"
    fs = FileSystemObjectStore(base_dir=str(tmp_path))

    assert fs.put(key, data) == key  # fs 返回键
    assert fs.get(key) == data
    assert fs.exists(key) is True
    assert fs.exists("missing/0") is False

    s3, stub = _stubbed_s3()
    stub.add_response("put_object", {}, expected_params={"Bucket": _BUCKET, "Key": key, "Body": data})
    stub.add_response(
        "get_object", {"ContentLength": len(data), "Body": io.BytesIO(data)}, expected_params={"Bucket": _BUCKET, "Key": key}
    )
    stub.add_response("head_object", {"ContentLength": len(data)}, expected_params={"Bucket": _BUCKET, "Key": key})
    stub.add_client_error(
        "head_object", expected_params={"Bucket": _BUCKET, "Key": "missing/0"}, service_error_code="404", http_status_code=404
    )
    with stub:
        # 逐观测等值：put 返回值、get 字节、exists 真/假 四步与 fs 完全一致（含缺失键=404→False）
        assert s3.put(key, data) == fs.put(key, data) == key
        assert s3.get(key) == fs.get(key) == data
        assert s3.exists(key) == fs.exists(key) is True
        assert s3.exists("missing/0") == fs.exists("missing/0") is False
    stub.assert_no_pending_responses()


def test_s3_missing_bucket_fails_loud_not_silent_fs() -> None:
    with pytest.raises(ProviderNotConfigured):
        S3ObjectStore(None)  # 空桶=构造即拒，绝不静默拿 fs/内存档（同 M4-an 静默错配危险类）


def test_s3_absent_driver_fails_loud_on_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)  # sys.modules=None → import boto3 抛 ImportError
    with pytest.raises(ProviderNotConfigured):
        S3ObjectStore("some-bucket")  # 缺驱动=构造即拒、不静默回落；未注入 client 才触懒导入


# ---------------- Kafka：pg_queue ↔ kafka(注入 fake) 同 MqPort 契约 ----------------
class _FakeLog:
    """进程内最小 broker 建模：append-only 日志 + 读取位点 + 已提交 offset。

    为什么必须有位点而不只是一个 deque：ADR-011 的租约语义（"未提交＝可重投"）恰好落在
    `poll` 推进位点、`commit` 推进已提交、`seek` 回退位点这三件事上；用 deque 弹取会把
    kafka 档也写成"取走即销毁"，双实现对账就再也照不出 pg 档的同类缺陷。
    """

    def __init__(self) -> None:
        self.messages: list[_FakeMsg] = []
        self.pos = 0  # 下一条 poll 的位点
        self.committed = 0  # 已确认到第几条（lag=len-committed，对齐 consumer lag 口径）


class _FakeMsg:
    def __init__(self, value: bytes, offset: int = -1) -> None:
        self._value = value
        self.offset = offset

    def value(self) -> bytes:
        return self._value


class _FakeProducer:
    def __init__(self, log: _FakeLog) -> None:
        self._log = log
        self.flushed = 0

    def produce(self, topic: str, value: bytes) -> None:
        self._log.messages.append(_FakeMsg(value, offset=len(self._log.messages)))

    def flush(self) -> None:
        self.flushed += 1


class _FakeConsumer:
    def __init__(self, log: _FakeLog) -> None:
        self._log = log
        self.subscribed: list[str] = []
        self.committed_offsets: list[int] = []
        self.seeked_offsets: list[int] = []

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed.extend(topics)

    def poll(self, timeout: float) -> _FakeMsg | None:
        if self._log.pos >= len(self._log.messages):
            return None  # 无新消息=超时返空（对齐 pg_queue 排空即返，非错误）
        msg = self._log.messages[self._log.pos]
        self._log.pos += 1
        return msg

    def commit(self, message: _FakeMsg, asynchronous: bool) -> None:
        self.committed_offsets.append(message.offset)
        self._log.committed = max(self._log.committed, message.offset + 1)

    def seek(self, message: _FakeMsg) -> None:
        self.seeked_offsets.append(message.offset)
        self._log.pos = min(self._log.pos, message.offset)  # 回退读取位＝该条及其后重投


def test_kafka_publish_drain_matches_pgqueue_semantics() -> None:
    payloads = [{"doc": "a", "op": "upsert", "n": 1}, {"doc": "b", "op": "delete", "tags": ["hr", "fin"]}]

    pg = PgQueueMq()
    log = _FakeLog()
    kq = KafkaMq(producer=_FakeProducer(log), consumer=_FakeConsumer(log), lag_provider=lambda _t: len(log.messages) - log.committed)
    topic = "canonical-events"

    for p in payloads:
        pg.publish(topic, p)
        kq.publish(topic, p)

    assert pg.depth(topic) == 2 == kq.depth(topic)  # 积压口径等值（注入 lag 映射未提交条数）
    assert pg.drain(topic, 1) == kq.drain(topic, 1) == [payloads[0]]  # 逐 dict 等值 + 顺序保持
    # 取走≠消费完：两档都必须把在租未确认的那条继续计作积压（旧口径在此归零＝卡住的批次从指标上蒸发）
    assert pg.depth(topic) == 2 == kq.depth(topic)  # 未取 1 + 在租 1：一条都没少
    assert pg.ack(topic, 1) == 0 == kq.ack(topic, 1)
    assert pg.drain(topic, 5) == kq.drain(topic, 5) == [payloads[1]]
    assert pg.depth(topic) == 1 == kq.depth(topic)
    assert pg.ack(topic, 1) == 0 == kq.ack(topic, 1)
    assert pg.depth(topic) == 0 == kq.depth(topic)
    assert pg.drain(topic, 3) == kq.drain(topic, 3) == []  # 排空后返空、非错误


def test_kafka_partial_ack_requeues_head_in_order_matches_pgqueue() -> None:
    """批中途失败＝至少一次的核心那半边：已完成前缀落定，失败条与其后全部**按原序**重投（ADR-011）。

    这是本刀的缺陷本体——旧 pg_queue 档 drain 即弹取，`run_once` 第 2 条抛错时第 3 条既没编译也不在积压，
    静默销毁。等值断言把两档钉成同一条契约：重投集合、次序、以及"已确认前缀不重做"三样都必须一致。
    """
    payloads = [{"i": 0}, {"i": 1}, {"i": 2}]
    topic = "canonical.fanout"

    pg = PgQueueMq()
    log = _FakeLog()
    consumer = _FakeConsumer(log)
    kq = KafkaMq(producer=_FakeProducer(log), consumer=consumer, lag_provider=lambda _t: len(log.messages) - log.committed)
    for p in payloads:
        pg.publish(topic, p)
        kq.publish(topic, p)

    for mq in (pg, kq):
        assert mq.drain(topic, 2) == [{"i": 0}, {"i": 1}]  # 第 2 条编译失败＝只 ack 前 1 条
        assert mq.ack(topic, 1) == 1
        assert mq.depth(topic) == 2  # 未确认的第 2 条 + 未取的第 3 条，都在账上
    assert pg.drain(topic, 5) == kq.drain(topic, 5) == [{"i": 1}, {"i": 2}]  # 重投不跳序、不重做 i0
    assert (consumer.committed_offsets, consumer.seeked_offsets) == ([0], [1])  # 逐条 commit/seek 的落点


def test_kafka_ack_out_of_range_fails_loud_same_wording_as_pgqueue() -> None:
    """两档越界口径逐字一致：同一条 `ValueError` 文案，否则换档会把"少确认"读成两种结果。"""
    log = _FakeLog()
    kq = KafkaMq(producer=_FakeProducer(log), consumer=_FakeConsumer(log))
    with pytest.raises(ValueError, match="在租 0 条，请求确认 1 条"):
        kq.ack("canonical-events", 1)


def test_kafka_wire_format_is_json_roundtrip_and_deterministic() -> None:
    # 证明真适配器确经 JSON 序列化层往返（pg_queue 直存 dict、kafka 必须编码信封）
    payload = {"z": 1, "a": "中文", "nested": {"k": [1, 2]}}
    raw = encode(payload)
    assert decode(raw) == payload  # 值等价
    assert raw == encode(payload)  # sort_keys → 逐字节确定（可对账）

    with pytest.raises(ProviderNotConfigured):  # 非对象信封（如数组）=契约破坏、不静默吞
        decode(b"[1,2,3]")


def test_kafka_no_broker_fails_loud_not_silent_pgqueue() -> None:
    with pytest.raises(ProviderNotConfigured):
        KafkaMq(None)  # 无 broker=构造即 producer→fail-loud，绝不静默拿 pg_queue（同 M4-ap 静默错配危险类）


def test_kafka_depth_without_lag_provider_fails_loud_not_fake_zero() -> None:
    mq = KafkaMq(producer=_FakeProducer(_FakeLog()), consumer=_FakeConsumer(_FakeLog()))  # 未注 lag_provider
    with pytest.raises(ProviderNotConfigured):
        mq.depth("canonical-events")  # 真实 consumer lag 须 broker/共享 offset，缺供给即拒、不伪报 0 积压
