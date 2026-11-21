"""C 区审计落盘（NFR-5 append-only）：熔断/越权/换载/参数变更事件写 JSONL，禁删改。

写侧只追加；归档/清理策略见 harness/file_lifecycle.yaml（protected_append_only 永不自动删）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.platform.types import AuditEvent


class AuditSink:
    def __init__(self, audit_dir: str = "workspace/audit") -> None:
        self._dir = Path(audit_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def write(self, event: AuditEvent) -> Path:
        path = self._dir / f"audit-{time.strftime('%Y%m%d')}.jsonl"
        record = event.model_copy(update={"ts": event.ts or time.time()})
        with path.open("a", encoding="utf-8") as fh:  # 只追加，绝不截断
            fh.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")
        return path

    def read_all(self) -> list[AuditEvent]:
        events: list[AuditEvent] = []
        for path in sorted(self._dir.glob("audit-*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(AuditEvent.model_validate(json.loads(line)))
        return events
