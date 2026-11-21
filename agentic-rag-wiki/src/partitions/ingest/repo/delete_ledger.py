"""A 区删除墓碑账（§10 软删除+保留期内可恢复 / §8.5 B1「删除入隔离待确认区」的 A 区回滚点，内存 stand-in）。

删除落地时不即刻物理销毁正文（正文仍留 objectstore，指针=可恢复源），本账按 (doc_id, version) 记墓碑信封
+ 被删真实 chunk_id + 保留期截止，提供「tombstone 恢复作业」的选单：保留期内可回滚重放，过窗即不可恢复。
跨进程/重启后的账实持久与真 30 天保留=真实 PG（放行项②）；此为 stand-in 上可跑通、可单测的回滚代码路径。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.platform.types import CanonicalEvent

# §10：30 天保留期（单机 dev 档口径，配置可换；真企业档由 PG 保留策略执法）。
RETAIN_SECONDS_30D = 30 * 24 * 3600


@dataclass
class Tombstone:
    event: CanonicalEvent
    chunk_ids: list[str]
    deleted_at: float
    retain_until: float
    restored: bool = False


class DeleteLedger:
    def __init__(self, *, retain_seconds: float = RETAIN_SECONDS_30D) -> None:
        self._retain = retain_seconds
        self._by_key: dict[tuple[str, int], Tombstone] = {}

    def record(self, event: CanonicalEvent, chunk_ids: list[str], *, deleted_at: float) -> None:
        """按 (doc_id, version) 记一条可恢复墓碑；同键重删覆盖为最新一次（幂等）。"""
        self._by_key[(event.doc_id, event.version)] = Tombstone(
            event=event,
            chunk_ids=list(chunk_ids),
            deleted_at=deleted_at,
            retain_until=deleted_at + self._retain,
        )

    def within_retention(self, now: float) -> list[Tombstone]:
        """仍在保留期且未恢复=可回滚（恢复作业选单）。"""
        return [t for t in self._by_key.values() if not t.restored and now < t.retain_until]

    def expired(self, now: float) -> list[Tombstone]:
        """已过保留期且未恢复=超出可回滚窗口（§10 回滚点失效，不臆造）。"""
        return [t for t in self._by_key.values() if not t.restored and now >= t.retain_until]

    def mark_restored(self, doc_id: str, version: int) -> None:
        t = self._by_key.get((doc_id, version))
        if t is not None:
            t.restored = True

    def keys_for(self, doc_id: str, version: int | None = None) -> list[tuple[str, int]]:
        """该文档在账的 (doc_id, version) 键（受众变更定位下架件用；含已过窗与已恢复者）。

        不过滤窗口/恢复态是刻意的：本方法只回答"这本账上有没有它"。**不返回墓碑对象本身**，
        受众变更不得改写墓碑信封——墓碑记的是"下架那一刻的事实"，改了账实就分叉；收紧走受众账（AclAudienceLedger）。
        """
        return [k for k in self._by_key if k[0] == doc_id and (version is None or k[1] == version)]

    def audience_for(self, doc_id: str, version: int) -> list[str] | None:
        """该墓碑在"下架那一刻"的真实受众（清单信封原值）；无该键返回 None。

        返回副本、不返回墓碑对象：受众变更要读这个实值当"前值"，但**不得改写**墓碑信封（`keys_for` 同口径）。
        这样"前值取实值不采信请求自述"在下架件上同样成立——调用方报个假 `acl_before` 也把不了账。
        """
        t = self._by_key.get((doc_id, version))
        return None if t is None else list(t.event.acl_tags)

    def stats(self, now: float) -> dict[str, int]:
        return {
            "recorded": len(self._by_key),
            "restored": sum(1 for t in self._by_key.values() if t.restored),
            "recoverable": len(self.within_retention(now)),
            "expired": len(self.expired(now)),
        }
