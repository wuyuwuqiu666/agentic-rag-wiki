"""Wiki 页契约（FR-WIKI-1~5；回写块 ACL 交集规则见 runtime/IngestionPort 实现）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

WikiPageStatus = Literal["draft", "in_review", "published", "stale", "retracted"]


class WikiSection(BaseModel):
    heading: str
    body: str = ""
    source_chunk_ids: list[str] = Field(default_factory=list)  # FR-WIKI-3 溯源不变


class WikiPage(BaseModel):
    page_id: str
    domain: str
    title: str
    status: WikiPageStatus = "draft"
    sections: list[WikiSection] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)
    acl_tags: list[str] = Field(default_factory=list)  # = 全部源 chunk acl 交集（F2）
    version: int = 1
    reviewer: str = ""
    triage_reason: str = ""  # FR-WIKI-4 可回放判定依据
