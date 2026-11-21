"""角色/域聚合画像存储（FR-MEM-1 长期记忆表侧的 dev 单机档落点）：可覆写、可衰减。

与 append-only 审计**分文件**（审计在 `service/audit_sink.py`，只追加禁删改；本表整表原子覆写），
承 FR-MEM-1"两套保留策略、两个熔断计数、绝不混存"的口径。PG 物理分表=放行项②。
个人粒度不可入库=类型层 `PortraitAgg`（scope 仅 role/domain、键须聚合前缀）已结构性阻断，本层不重复执法。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.platform.types import PortraitAgg


class DevPortraitStore:
    """单文件 JSON 快照档（演示/开发）。读写即整表加载/原子覆写，损坏文件 fail-loud 不静默当空表。"""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def all(self) -> list[PortraitAgg]:
        return list(self._by_key().values())

    def get(self, key: str) -> PortraitAgg | None:
        return self._by_key().get(key)

    def upsert(self, agg: PortraitAgg) -> None:
        """按聚合键覆写（长期表可衰减可覆写，与审计的只追加相反）；不追加历史版本。"""
        data = self._by_key()
        data[agg.key] = agg
        self._write(data)

    def _by_key(self) -> dict[str, PortraitAgg]:
        if not self._path.is_file():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))  # 坏文件即抛，不吞成空画像表
        items = raw["portraits"] if isinstance(raw, dict) and "portraits" in raw else raw
        if not isinstance(items, list):
            raise ValueError(f"画像表结构非法（期望列表）：{self._path}")
        return {p.key: p for p in (PortraitAgg.model_validate(item) for item in items)}

    def _write(self, data: dict[str, PortraitAgg]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"portraits": [p.model_dump() for p in data.values()]}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)  # 原子换入：不留半写状态
