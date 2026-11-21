"""A 区 repo：受众变更前值账（acl_change 的"从哪个域收到哪个域"与最新域，内存 stand-in）。

为什么需要这本账（M4-cq，审计 #3）：`acl_change` 此前只有两本可查的账——清单信封（改写后即新值、无前值）
与删除墓碑（存的是**下架那一刻**的受众）。于是出现两类静默失真：

1. 文档已下架时清单里没有人，收紧无处可写＝no-op，而墓碑仍带旧域，`restore_deletes` 一重放就把收紧前的
   宽受众灌回索引＝权限收紧被恢复动作撤销；
2. 正文指针缺失时收紧计入 `acl_pending` 未落清单，之后 `compensate()` 按清单旧域重放，同样回落。

本账把"最新受众"单独记一笔，重放口（`_reindex_event`，恢复/补偿/ACL 重写三路共用）**只问这一本**，
故收紧一旦入账就不会被任何后续重放撤销；`before→after` 的历史留档让运维能问出改动依据。
跨进程持久与真权限行（效应①）＝真实 PG＝放行项②；专枚审计 kind（谁改的）属 E3 冻结 Literal＝放行项㉑。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AclAudienceEntry:
    """一条受众变更（append-only）：`(doc_id, version)` 从 `before` 收到 `after`，落点如实标注。"""

    doc_id: str
    version: int
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    ts: float = 0.0
    applied_to: str = "inventory"


class AclAudienceLedger:
    def __init__(self) -> None:
        self._history: list[AclAudienceEntry] = []
        # 最新受众按 (doc_id, version) 记：版本是受众的归属单位（同 doc 不同版本可不同域），不按 doc 合并不覆盖版本粒度。
        self._latest: dict[tuple[str, int], list[str]] = {}

    def record(
        self,
        doc_id: str,
        version: int,
        before: list[str],
        after: list[str],
        *,
        ts: float = 0.0,
        applied_to: str = "inventory",
    ) -> None:
        """入账即改最新受众：**无论索引那一路改没改成**（`applied_to` 只描述落点，不改变"以后重放按此域"）。

        理由＝受众收紧的意图来自变更事件，重放必须服从最新意图而不是服从"上次改失败时留下的旧值"。
        """
        self._history.append(
            AclAudienceEntry(
                doc_id=doc_id,
                version=version,
                before=list(before),
                after=list(after),
                ts=ts,
                applied_to=applied_to,
            )
        )
        self._latest[(doc_id, version)] = list(after)

    def current(self, doc_id: str, version: int) -> list[str] | None:
        """该 (doc_id, version) 当前应当的受众；从未变更过＝None（调用方按信封原受众走）。"""
        latest = self._latest.get((doc_id, version))
        return None if latest is None else list(latest)

    def history(self, doc_id: str, version: int | None = None) -> list[AclAudienceEntry]:
        out = [e for e in self._history if e.doc_id == doc_id and (version is None or e.version == version)]
        return sorted(out, key=lambda e: (e.version, e.ts))

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._history), "docs": len({e.doc_id for e in self._history})}
