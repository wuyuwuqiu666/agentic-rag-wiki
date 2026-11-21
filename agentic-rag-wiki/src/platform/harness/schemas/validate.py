"""types 契约完整性机检（护栏产物 #3，PRD §9）。

`python -m src.platform.harness.schemas.validate`
校两件事：①platform.types 复导出全部一级契约；②关键契约的必填字段不缺失
（字段漂移=A/B 区共享事实源被单方面改动，R7 冻结前的守护）。
"""

from __future__ import annotations

import sys

from pydantic import BaseModel

from src.platform.types import (
    Answer,
    AuditEvent,
    BreakerDecision,
    CanonicalDocument,
    CanonicalEvent,
    CanonicalNode,
    ChangeEvent,
    Chunk,
    ChunkDecision,
    Document,
    NodeDecision,
    ParamProposal,
    PortraitAgg,
    WikiPage,
    WorkingMemory,
)

# 一级/关键契约 → 必填字段（缺任一即视为契约漂移）
REQUIRED_FIELDS: dict[str, set[str]] = {
    "Document": {"doc_id"},
    "CanonicalDocument": {"doc_id", "version"},
    "CanonicalEvent": {"doc_id", "version", "store_key", "checksum"},  # F1 指针信封
    "Chunk": {"chunk_id", "doc_id", "doc_version", "seq", "text", "acl_tags", "embedding_model"},
    "ChangeEvent": {"doc_id", "kind"},  # F3 acl_change 载体
    "BreakerDecision": {"breaker", "tripped", "action", "level"},
    "WikiPage": {"page_id", "domain", "title", "acl_tags"},  # F2 回写 ACL 交集
    "Answer": {"question", "citations"},  # FR-ASK-2 引用强制
    "AuditEvent": {"kind"},
    "ParamProposal": {"param", "param_class"},
    "PortraitAgg": {"scope", "key"},  # FR-MEM-2① 聚合粒度护栏
    "WorkingMemory": {"session_id", "individual_id", "ttl_seconds"},  # FR-MEM-2② 会话 TTL（过期即焚前提）
}
_MODELS: dict[str, type[BaseModel]] = {
    "Document": Document,
    "CanonicalDocument": CanonicalDocument,
    "CanonicalEvent": CanonicalEvent,
    "CanonicalNode": CanonicalNode,  # M4-ck：决议契约三级入 E3 快照（ADR-010 已证明它正是会被改的模型）
    "NodeDecision": NodeDecision,
    "ChunkDecision": ChunkDecision,
    "Chunk": Chunk,
    "ChangeEvent": ChangeEvent,
    "BreakerDecision": BreakerDecision,
    "WikiPage": WikiPage,
    "Answer": Answer,
    "AuditEvent": AuditEvent,
    "ParamProposal": ParamProposal,
    "PortraitAgg": PortraitAgg,
    "WorkingMemory": WorkingMemory,
}


def check() -> list[str]:
    problems: list[str] = []
    for name, fields in REQUIRED_FIELDS.items():
        model = _MODELS.get(name)
        if model is None:
            problems.append(f"契约缺失：{name} 未在 types 导出")
            continue
        present = set(model.model_fields)
        missing = fields - present
        if missing:
            problems.append(f"{name} 必填字段漂移：缺 {sorted(missing)}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("schemas.validate 违规：")
        for p in problems:
            print("  -", p)
        return 1
    print("schemas.validate：types 契约字段完整。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
