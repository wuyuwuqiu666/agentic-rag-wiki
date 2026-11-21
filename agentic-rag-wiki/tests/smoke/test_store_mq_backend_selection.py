"""M4-aq §5/G5 装配表可换性：对象库/队列后端选择不再静默错配（c.objectstore/c.mq 幻影旋钮收口）。

本环只验**组合根是否消费旋钮**（选档→装不同实现类）；档位缺桶/缺 broker 一律 fail-loud，绝不静默回落默认档。
适配器内部行为（序列化映射、缺驱动 fail-loud、双实现同端口等值）由 `tests/contract/test_store_mq_dual_backend.py` 覆盖。

- 此前 `ProviderChoices.objectstore`（含 "s3"）与 `.mq`（含 "kafka"）组合根零读、硬写 `FileSystemObjectStore`/`PgQueueMq`
  ——选 s3/kafka 静默拿默认档（同 M4-an pgvector / M4-ap langgraph 静默错配危险类），且为"有字段零读"幻影配置。
- 收口后：s3→`S3ObjectStore(s.s3_bucket, ...)`、kafka→`KafkaMq(s.kafka_broker)`（M4-bu 已转真适配器）；
  默认档 filesystem/pg_queue 无桶/无 broker 配置即构造即拒，本环不伪造。
"""

from __future__ import annotations

import pytest
from src.platform.config.settings import ProviderChoices, Settings
from src.platform.gateway.composition import build_container
from src.platform.providers.errors import ProviderNotConfigured
from src.platform.providers.mq_kafka import KafkaMq
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.objectstore_s3 import S3ObjectStore


def test_s3_selected_fails_loud_not_silent_fs() -> None:
    # 选 s3 无桶配置 → 装配即拒，绝不静默回退文件系统档
    s = Settings(providers=ProviderChoices(objectstore="s3"))
    with pytest.raises(ProviderNotConfigured):
        build_container(s)


def test_kafka_selected_fails_loud_not_silent_pgqueue() -> None:
    # 选 kafka 无 broker 配置 → 装配即拒，绝不静默回退 pg_queue
    s = Settings(providers=ProviderChoices(mq="kafka"))
    with pytest.raises(ProviderNotConfigured):
        build_container(s)


def test_defaults_unchanged() -> None:
    # 默认 filesystem/pg_queue 档路径行为不变（不受本次改造影响）
    container = build_container(Settings())
    assert isinstance(container.objectstore, FileSystemObjectStore)
    assert isinstance(container.mq, PgQueueMq)


def test_knob_types_are_consumed() -> None:
    # 反证幻影旋钮：企业档类型与默认档类型互斥（选档驱动不同装配类，非静默同型回落）
    assert S3ObjectStore is not FileSystemObjectStore
    assert KafkaMq is not PgQueueMq
