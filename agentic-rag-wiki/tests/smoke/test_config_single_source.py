"""M4-aj §8.8/NFR-8 配置单一口径机检：两个"声明却零消费"的幻影旋钮转为真实消费者。

离线零外部依赖：断言的是反推公式与单值一致性的口径正确性，非真机显存实测（放行档）。
- NFR-8：`物理显存 × vram_safety_factor` 反推 batch 上界（`safe_batch_for_vram` + 装配 `_local_batch_safe`）。
- §8.8：并发上限 `Settings.inflight_llm_limit` 与 `b2.yaml` B2 `hard` 同值（`check_capacity_single_source`），防两处"8"静默漂移。
"""

from __future__ import annotations

import pytest
from src.platform.config.settings import Settings
from src.platform.gateway.composition import _local_batch_safe
from src.platform.harness.breaker_rules import validate
from src.platform.providers.embedding_local import safe_batch_for_vram


# ---------- NFR-8 显存反推（vram_safety_factor 的真实消费点） ----------
def test_safe_batch_for_vram_applies_factor() -> None:
    # 8192MB × 0.9 = 7372MB 可用 ÷ 225MB/批 = 32 批（与出厂 local_batch_safe=32 同量级，P3 实测锚）
    assert safe_batch_for_vram(8192, 225, safety_factor=0.9) == 32
    # 系数换值即改结果——证明 vram_safety_factor 确被消费、非装饰性字段
    assert safe_batch_for_vram(10000, 100, safety_factor=0.5) == 50
    assert safe_batch_for_vram(10000, 100, safety_factor=0.9) == 90


def test_safe_batch_for_vram_rejects_invalid_inputs() -> None:
    for bad in ((0, 100, 0.9), (1000, 0, 0.9)):
        with pytest.raises(ValueError):
            safe_batch_for_vram(bad[0], bad[1], safety_factor=bad[2])
    for factor in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            safe_batch_for_vram(1000, 100, safety_factor=factor)


def test_local_batch_safe_derives_when_budget_given() -> None:
    s = Settings(local_vram_mb=8192, local_per_batch_mb=225, vram_safety_factor=0.9)
    assert _local_batch_safe(s) == 32  # 齐备→反推
    assert _local_batch_safe(Settings()) == Settings().local_batch_safe  # 默认（无预算）→配置化值，行为不变
    assert _local_batch_safe(Settings(local_vram_mb=8192)) == Settings().local_batch_safe  # 仅一半→回退


# ---------- §8.8 容量单一口径不变式（inflight_llm_limit 的真实消费点） ----------
def test_capacity_single_source_flags_drift() -> None:
    assert validate.check_capacity_single_source(8, 8) is None
    msg = validate.check_capacity_single_source(16, 8)
    assert msg is not None and "16" in msg and "8" in msg
    assert validate.check_capacity_single_source(None, 8) is None  # 无法比对→不臆断


def test_real_repo_has_no_capacity_drift() -> None:
    # 真实仓库：Settings 默认 8 == b2.yaml B2 hard 8 → 全量 check 不含容量漂移项
    problems = validate.check()
    assert not [p for p in problems if "容量单一口径漂移" in p]
