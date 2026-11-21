"""跨分区共享词汇（PRD §5 platform/types）。

P1 纯净内核：本层只允许标准库 + pydantic（唯一例外条款）。禁止 import 任何 SDK 或其他层。
"""

from src.platform.types.answer import AgentState, Answer, AuditEvent, Citation
from src.platform.types.breaker import (
    BreakerDecision,
    BreakerLevel,
    MetricSample,
    TripRecord,
)
from src.platform.types.change import ChangeEvent, ChangeKind
from src.platform.types.document import (
    CanonicalDocument,
    CanonicalEvent,
    CanonicalNode,
    Chunk,
    ChunkDecision,
    ChunkType,
    Document,
    NodeDecision,
    PageSpan,
)
from src.platform.types.memory import ParamProposal, PortraitAgg, WorkingMemory
from src.platform.types.wiki import WikiPage, WikiPageStatus, WikiSection

__all__ = [
    "AgentState",
    "Answer",
    "AuditEvent",
    "BreakerDecision",
    "BreakerLevel",
    "CanonicalDocument",
    "CanonicalEvent",
    "CanonicalNode",
    "Citation",
    "ChangeEvent",
    "ChangeKind",
    "Chunk",
    "ChunkDecision",
    "ChunkType",
    "Document",
    "MetricSample",
    "NodeDecision",
    "PageSpan",
    "ParamProposal",
    "PortraitAgg",
    "TripRecord",
    "WikiPage",
    "WikiPageStatus",
    "WikiSection",
    "WorkingMemory",
]
