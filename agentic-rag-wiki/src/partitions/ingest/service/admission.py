"""A 区准入：ACL 完备 + DLP 过滤（T2 外部输入先校验才进内核；B4 注入/外泄前置）。

不合规块隔离（quarantine），不静默入库。审计事件由调用方落盘（C 区 audit sink）。
DLP 口径由 `service.dlp` 与答案出口共享（一处定义、入口拦=出口也拦，防口径漂移成泄露通道）。
"""

from __future__ import annotations

from src.partitions.ingest.service import dlp
from src.platform.types import Chunk


class AdmissionResult:
    def __init__(self, accepted: list[Chunk], quarantined: list[Chunk]) -> None:
        self.accepted = accepted
        self.quarantined = quarantined


def admit(chunks: list[Chunk]) -> AdmissionResult:
    accepted: list[Chunk] = []
    quarantined: list[Chunk] = []
    for c in chunks:
        # ACL 完备：无标签=不可检索，挡在门外（空 acl 视作公开域，仍放行但记 source）
        if c.acl_tags is None:  # pragma: no cover - pydantic 已保证 list
            quarantined.append(c)
            continue
        if dlp.find_secrets(c.text):  # 单一口径判定（命中列表非空即含明文口令）：入口经此共享谓词，防入口/出口各自摸 SECRET_RE 漂移
            quarantined.append(c)
            continue
        accepted.append(c)
    return AdmissionResult(accepted, quarantined)
