"""A 区问答检索服务（FR-ASK）：召回融合与置信口径等纯逻辑。

检索面统一在 A 区索引；wiki 优先=排序属性（authority）。此处零 SDK、跨区不 import（check_imports 执法）。
"""

from src.partitions.ingest.service.ask import citation, fusion, planner, policy

__all__ = ["citation", "fusion", "planner", "policy"]
