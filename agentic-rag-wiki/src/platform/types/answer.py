"""审计与答案契约（NFR-5 不可篡改追加写 / FR-ASK 引用强制）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """审计事件（C 区 audit 表，append-only，禁删改）。"""

    kind: Literal[
        "breaker_trip",
        "breaker_reset",
        "acl_denied",
        "quarantine",
        "param_change",
        "external_call",
        "provider_fail_loud",
        "wiki_publish",
        "wiki_invalidate",
    ]
    actor: str = "system"
    partition: str = "platform"
    doc_id: str = ""
    detail: str = ""
    ts: float = 0.0
    trace_id: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    """chunk 级出处（FR-ASK-2：无出处句必被拦截）。"""

    chunk_id: str
    doc_id: str
    doc_version: int
    breadcrumb: list[str] = Field(default_factory=list)
    page_no: int | None = None
    authority: Literal["source", "wiki"] = "source"


class Answer(BaseModel):
    question: str
    text: str = ""
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = 0.0
    refused: bool = False
    hops: int = 1
    degraded: bool = False
    trace_id: str = ""


class AgentState(BaseModel):
    """agent 问答状态图的状态（types 定义，换编排框架不断点丢数据）。"""

    question: str
    user_id: str = ""
    acl_tags: list[str] = Field(default_factory=list)
    hops: int = 0
    max_hops: int = 8
    retrieved: list[str] = Field(default_factory=list)
    draft: str = ""
    confidence: float = 0.0
    needs_human: bool = False
