"""M4-cp（审计 #6）：B 区消费侧的两条次序不变式——**判门在 drain 之前** ＋ **取走不等于消费完**。

修前的真实缺陷（不是假想）：`WikiLoop.run_once` 先 `drain`（旧实现弹取即销毁）最多 1000 条再逐条写页，
① 全程没有 B0 总闸（A 区 `admit`/`withdraw` 有，B 区回路没有）＝拉闸期照样把 fan-out 吃掉，
"冻结全部写侧"只剩索引那一半；② 循环中途任何 fail-loud（信封破坏、正文读不出）时，
已 drain 的剩余事件既不在页上也不在积压里＝静默销毁，`depth` 还显示"追平了"。

锁四条：
① 冻结期**零 drain、零消费**（门在取事件之前，队列位点不动）；
② 中途失败＝已完成前缀落定、失败条与其后全部按原序退回队首（至少一次，不重做、不跳序）；
③ 毒事件卡住的头阻如实可测（每轮都在同一条上炸，不静默跳过、不假装跑完）——处置＝修事件源，
   死信队列属企业档放行项⑯，本环不伪造"自动旁路坏消息"的能力；
④ 组合根给 B 区的是**同一册**总闸（另立一本＝只冻住 A 区，缺陷换层复现）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from src.partitions.wiki.runtime.loop import FANOUT_TOPIC, WikiLoop
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.providers.errors import BreakerTripped
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.types import BreakerDecision, CanonicalEvent, Document

_TEXT = "# 差旅报销政策\n\n" + "\n\n".join(f"第{i}条：出差交通与住宿的报销标准与审批说明。" for i in range(40))
_POISON = {"doc_id": "broken", "op": "upsert"}  # 缺 version/domain=信封破坏（外部输入不得进纯净内核）


def _b0() -> BreakerDecision:
    return BreakerDecision(
        breaker="B0", tripped=True, action="block", metric="unreset_hard_trips", value=3.0, reason="演练：总闸已拉"
    )


class _NullIngestion:
    """本套只验消费侧次序，不验回写：`run_once` 不经 `IngestionPort`（那是 publish/撤回那条缝）。"""

    def admit(self, event: object, chunks: list[object]) -> int:
        return 0

    def withdraw(self, chunk_ids: list[str], acl_tags: list[str]) -> int:
        return 0

    def is_indexed(self, doc_id: str) -> bool:
        return True


class _CountingMq:
    """包一层真 dev 队列，只为数 `drain`/`ack` 调用次数：①的"零 drain"光看积压读数区分不出来（未 ack 也计账）。"""

    def __init__(self) -> None:
        self._inner = PgQueueMq()
        self.drains = 0
        self.acks = 0

    def publish(self, topic: str, payload: dict[str, object]) -> None:
        self._inner.publish(topic, payload)

    def drain(self, topic: str, max_items: int) -> list[dict[str, object]]:
        self.drains += 1
        return self._inner.drain(topic, max_items)

    def ack(self, topic: str, count: int) -> int:
        self.acks += 1
        return self._inner.ack(topic, count)

    def depth(self, topic: str) -> int:
        return self._inner.depth(topic)


class _Guard:
    """可控总闸：`open=False` 时 `ensure_write_allowed` 抛 B0，等价于组合根里累积未复位硬断的升级态。"""

    def __init__(self, *, open_: bool = True) -> None:
        self.open = open_
        self.partitions: list[str] = []

    def ensure_write_allowed(self, partition: str = "platform") -> None:
        self.partitions.append(partition)
        if not self.open:
            raise BreakerTripped(_b0())


def _store(tmp_path) -> FileSystemObjectStore:
    return FileSystemObjectStore(str(tmp_path / "obj"))


def _loop(mq: object, tmp_path, *, guard: object = None) -> WikiLoop:
    return WikiLoop(mq, _NullIngestion(), _store(tmp_path), guard=guard)  # type: ignore[arg-type]


def _seed(mq: object, tmp_path, doc_id: str = "travel") -> CanonicalEvent:
    """造一条真会入选编译的 fan-out 事件（正文进 objectstore、信封只带指针＝F1 语义）。"""
    ev = CanonicalEvent(doc_id=doc_id, version=1, domain="fin", doc_type="policy", acl_tags=["fin"], token_count=800)
    _store(tmp_path).put(f"{doc_id}/1", _TEXT.encode("utf-8"))
    ev = ev.model_copy(update={"store_key": f"{doc_id}/1"})
    mq.publish(FANOUT_TOPIC, ev.model_dump())  # type: ignore[attr-defined]
    return ev


def test_frozen_master_breaker_drains_and_consumes_nothing(tmp_path) -> None:
    """①：B0 拉闸期 `run_once` 抛错前**没碰过队列**——drain/ack 计数均为 0、草稿 0、积压原封不动。

    锁的是次序而不是结果：门只要写在 drain 之后，冻结一轮就把整批 fan-out 消费成看不见的工作。
    """
    mq = _CountingMq()
    _seed(mq, tmp_path)
    loop = _loop(mq, tmp_path, guard=_Guard(open_=False))

    with pytest.raises(BreakerTripped):
        loop.run_once()
    assert (mq.drains, mq.acks) == (0, 0)
    assert loop.drafts == []
    assert mq.depth(FANOUT_TOPIC) == 1  # 冻结=推迟，不是丢弃


def test_reset_lets_the_deferred_round_run_and_close_the_lease(tmp_path) -> None:
    """①的成对半：门开着就把租约走完——同一条事件复位后正常编译、积压归零（不是永久卡死）。"""
    mq = _CountingMq()
    _seed(mq, tmp_path)
    guard = _Guard(open_=False)
    loop = _loop(mq, tmp_path, guard=guard)
    with pytest.raises(BreakerTripped):
        loop.run_once()

    guard.open = True
    assert loop.run_once() == 1
    assert len(loop.drafts) == 1
    assert mq.depth(FANOUT_TOPIC) == 0  # drain+ack 走完租约才算消费完
    assert (mq.drains, mq.acks) == (1, 1)


def test_midloop_failure_returns_the_rest_to_the_head(tmp_path) -> None:
    """②：第 2 条信封破坏＝第 1 条已落的草稿**不重做**，第 2、3 条按原序退回队首、仍计积压。"""
    mq = PgQueueMq()
    _seed(mq, tmp_path)
    mq.publish(FANOUT_TOPIC, _POISON)
    third = _event_sop().model_dump()
    mq.publish(FANOUT_TOPIC, third)

    loop = _loop(mq, tmp_path)
    with pytest.raises(ValidationError):
        loop.run_once()
    assert len(loop.drafts) == 1  # 已完成前缀已确认
    assert mq.depth(FANOUT_TOPIC) == 2  # 旧实现此处=0（两条凭空蒸发）
    assert mq.drain(FANOUT_TOPIC, 5) == [_POISON, third]  # 不跳序、不重复第一条


def test_poison_event_blocks_the_head_loudly_every_round(tmp_path) -> None:
    """③：毒事件在队首＝每轮都在同一条上炸，且不留半成品（头阻如实可见，不静默跳过、不假装跑完）。"""
    mq = PgQueueMq()
    mq.publish(FANOUT_TOPIC, _POISON)
    loop = _loop(mq, tmp_path)
    for _ in range(2):
        with pytest.raises(ValidationError):
            loop.run_once()
        assert loop.drafts == []
        assert mq.depth(FANOUT_TOPIC) == 1  # 未确认＝一直在账上，运维看得见


def test_composition_gives_b_region_the_same_master_breaker(tmp_path) -> None:
    """④：组合根把**同一册** guard 注进 B 区，且真 B0 态下 fan-out 一条没少、复位后补编译。"""
    c = build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), shadow_breakers=""))
    assert c.wiki._guard is c.guard
    c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )
    for _ in range(3):  # 未复位硬断累积→B0 升级（与 A 区同一册台账）
        with pytest.raises(BreakerTripped):
            c.guard.check("batch_delete_ratio", 0.5, "B1", partition="ingest")

    with pytest.raises(BreakerTripped):
        c.wiki.run_once()
    assert c.wiki.drafts == []
    assert c.mq.depth(FANOUT_TOPIC) == 1

    c.guard.reset_breaker("B1", by="sre-a", reason="runbook 处置完毕")
    assert c.wiki.run_once() == 1
    assert c.mq.depth(FANOUT_TOPIC) == 0


def test_gate_is_labelled_with_the_wiki_partition(tmp_path) -> None:
    """审计分区归属：B 区走总闸落在 `partition="wiki"`，否则处置时按分区找错作业（A 区账≠B 区账）。"""
    mq = PgQueueMq()
    guard = _Guard()
    _loop(mq, tmp_path, guard=guard).run_once()
    assert guard.partitions == ["wiki"]


def _event_sop() -> CanonicalEvent:
    ev = CanonicalEvent(doc_id="sop", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], token_count=800)
    return ev.model_copy(update={"store_key": "sop/1"})
