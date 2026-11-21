"""M4-an §5/G5 装配表可换性 + pg_dsn 幻影键收口：pgvector 档不再静默落内存。

离线零外部依赖（不装 psycopg、不起 PG）：
- 此前 `providers.vectorstore=="pgvector"` 在组合根走 else 分支→ `MemoryVectorStore`（选了 pgvector 却拿内存档，
  违 G5"换档只改本表·业务代码零改动"、且 `Settings.pg_dsn` 全库零消费）。
- 收口后：pgvector→`PgVectorStore(s.pg_dsn)` 占位（缺 DSN/缺驱动 fail-loud，真实实现=放行项②起 PG，本环不伪造）。
"""

from __future__ import annotations

import pytest
from src.platform.config.settings import ProviderChoices, Settings
from src.platform.gateway.composition import build_container
from src.platform.providers.errors import ProviderNotConfigured
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.providers.vectorstore_pgvector import PgVectorStore


def test_pgvector_without_dsn_fails_loud() -> None:
    # 选 pgvector 没配 PG_DSN → 装配期即拒，绝不静默回退内存（E2/NFR-8 同 Milvus 口径）
    s = Settings(providers=ProviderChoices(vectorstore="pgvector"))
    with pytest.raises(ProviderNotConfigured):
        build_container(s)


def test_pgvector_selected_is_not_memory() -> None:
    # 反证旧缺陷：pgvector 档不得等于内存档类型（旧代码 else 分支会误给 Memory）
    with pytest.raises(ProviderNotConfigured):
        PgVectorStore(None)
    assert not isinstance(MemoryVectorStore(), PgVectorStore)


def test_memory_backend_still_default() -> None:
    # 默认 memory 档路径行为不变（不受本次三分支改造影响）
    container = build_container(Settings())
    assert isinstance(container.vectorstore, MemoryVectorStore)


def test_milvus_backend_still_gated() -> None:
    # milvus 档维持原 fail-loud（缺 URI）——三分支重构未回退既有门
    s = Settings(providers=ProviderChoices(vectorstore="milvus"))
    with pytest.raises(ProviderNotConfigured):
        build_container(s)
