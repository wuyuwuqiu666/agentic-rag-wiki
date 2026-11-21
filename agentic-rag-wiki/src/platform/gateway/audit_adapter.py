"""AuditPort 实现适配器（组合根）：把分区经端口发出的事件落到 C 区 append-only sink。

分区只见 AuditPort、不见 memory 分区（跨区三禁之①由此守）。
"""

from __future__ import annotations

from src.partitions.memory.service.audit_sink import AuditSink
from src.platform.types import AuditEvent


class AuditAdapter:
    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink

    def emit(self, event: AuditEvent) -> None:
        self._sink.write(event)
