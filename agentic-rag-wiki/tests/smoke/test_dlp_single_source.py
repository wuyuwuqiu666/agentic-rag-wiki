"""M4-ak §8.5/§12 DLP 判定单一口径：入口准入经 `find_secrets` 共享谓词，防入口/出口各摸 SECRET_RE 漂移。

`dlp` 模块自陈"一处定义=防入口/出口口径漂移"，但入口此前直接 `SECRET_RE.search`、把声明的共享谓词
`find_secrets` 悬空（零消费零测试）。本环令入口改经 `find_secrets`，并锁"入口判定"与"出口脱敏命中数"
对同一串同真同假——正则语义若两处背离即回归红。
"""

from __future__ import annotations

from src.partitions.ingest.service import admission, dlp
from src.platform.types import Chunk


def _chunk(cid: str, text: str) -> Chunk:
    return Chunk(chunk_id=cid, doc_id="d", doc_version=1, seq=0, text=text, acl_tags=["hr"])  # type: ignore[arg-type]


def test_find_secrets_returns_matched_keywords() -> None:
    assert dlp.find_secrets("password: hunter2") == ["password"]
    assert dlp.find_secrets("api_key=abc token: xyz") == ["api_key", "token"]  # 保序取每处关键字名
    assert dlp.find_secrets("普通文本，无口令") == []


def test_entry_predicate_and_egress_hitcount_agree() -> None:
    # 单一口径护栏：入口"是否含密"（find_secrets 非空）须与出口"脱敏命中数>0"同真同假。
    for text in (
        "plain body text",
        "password: p",
        "泄露 api_key=K 与 secret=S",
        "just a number 42",
        "token: TAIL",
    ):
        at_entry = bool(dlp.find_secrets(text))
        at_egress = dlp.redact(text)[1] > 0
        assert at_entry == at_egress, f"入口/出口口径背离：{text!r}"


def test_admission_quarantines_via_shared_predicate() -> None:
    result = admission.admit([_chunk("d#0", "正常块"), _chunk("d#1", "password: leak")])
    assert [c.chunk_id for c in result.accepted] == ["d#0"]
    assert [c.chunk_id for c in result.quarantined] == ["d#1"]  # 命中 find_secrets 即挡在门外
