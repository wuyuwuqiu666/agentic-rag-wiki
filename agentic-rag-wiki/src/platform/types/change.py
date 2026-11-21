"""变更事件契约（FR-ING-2/5：CDC 三类事件，acl_change 四效应收口 F3）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChangeKind = Literal["upsert", "delete", "acl_change", "domain_move"]


class ChangeEvent(BaseModel):
    """源系统变更事件（进 A 区变更流，B1 判定挂在此处入口）。"""

    doc_id: str
    kind: ChangeKind
    version: int | None = None
    acl_tags_before: list[str] = Field(default_factory=list)
    acl_tags_after: list[str] = Field(default_factory=list)
    domain_before: str | None = None
    domain_after: str | None = None
    ts: float = 0.0
    batch_id: str = ""
