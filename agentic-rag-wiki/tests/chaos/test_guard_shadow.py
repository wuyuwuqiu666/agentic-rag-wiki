"""M1 审计流上线 + B1 shadow/硬断（FR-MEM-1：触发即落 C 区 append-only 审计）。"""

from __future__ import annotations

import pytest
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.providers.errors import BreakerTripped
from src.platform.types import ChangeEvent


def _container(tmp_path, shadow: str):
    return build_container(
        Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), shadow_breakers=shadow)
    )


def test_b1_shadow_records_but_does_not_block(tmp_path) -> None:
    c = _container(tmp_path, "B1,B5")
    changes = [ChangeEvent(doc_id=f"d{i}", kind="delete") for i in range(10)] + [ChangeEvent(doc_id="k", kind="upsert")]
    c.ingest.apply_changes(changes)  # 删除占比 10/11 远超硬阈，但 M1 shadow 只记不拦
    trips = [e for e in c.audit.read_all() if e.kind == "breaker_trip"]
    assert trips, "熔断触发须落审计"
    assert trips[0].extra["shadow"] is True
    assert trips[0].extra["action"] == "observe"


def test_b1_hard_blocks_when_shadow_cleared(tmp_path) -> None:
    c = _container(tmp_path, "")  # M4 全硬断口径：清空 shadow
    changes = [ChangeEvent(doc_id=f"d{i}", kind="delete") for i in range(10)]
    with pytest.raises(BreakerTripped):
        c.ingest.apply_changes(changes)
    assert any(e.kind == "breaker_trip" for e in c.audit.read_all())  # 阻断同样先审计留痕
