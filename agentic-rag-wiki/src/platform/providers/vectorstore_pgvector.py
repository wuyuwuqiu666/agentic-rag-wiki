"""VectorStorePort 回迁档：pgvector（PG 扩展）。M0 仅登记端口 + 消费 `Settings.pg_dsn`，需真实 PG 方可实例化。

ADR-002：Milvus 为执行档默认向量库，pgvector 是"回迁能力"档——同 `VectorStorePort` 契约、由装配表切换。
此前 `c.vectorstore=="pgvector"` 在组合根静默落到 `MemoryVectorStore`（选了 pgvector 却拿到内存档，违 G5"换档只改本表"），
且 `Settings.pg_dsn` 全库零消费（幻影键，同 milvus_uri 家族）。本占位镜像 Milvus：缺 DSN/缺驱动即 fail-loud，
真实实现（psycopg + pgvector 列/索引）在 M1 装配表冻结 + 放行项②（起 PG）后接入。
"""

from __future__ import annotations

from src.platform.providers.errors import ProviderNotConfigured
from src.platform.types import Chunk


class PgVectorStore:
    def __init__(self, dsn: str | None) -> None:
        if not dsn:
            raise ProviderNotConfigured("pgvector 需 PG_DSN（M1 接入 / 放行项②起 PG）")
        try:
            import psycopg  # noqa: F401, PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ProviderNotConfigured("pgvector 档需 psycopg + pgvector 扩展（pip install .[pg]）") from exc
        self._dsn = dsn

    def upsert(self, chunks: list[Chunk]) -> int:
        raise ProviderNotConfigured("pgvector 实现在 M1 装配表冻结 + 起 PG 后接入")

    def delete(self, chunk_ids: list[str]) -> int:
        raise ProviderNotConfigured("pgvector 实现在 M1 装配表冻结 + 起 PG 后接入")

    def search(self, vector: list[float], topk: int, acl_tags: list[str]) -> list[Chunk]:
        raise ProviderNotConfigured("pgvector 实现在 M1 装配表冻结 + 起 PG 后接入")

    def alias_swap(self, old: str, new: str) -> None:
        raise ProviderNotConfigured("pgvector 版本化切换（schema/视图）在 M1 接入")

    def all_ids(self) -> list[str]:
        raise ProviderNotConfigured("pgvector 全量清单枚举在 M1 接入（对账 B5 依赖）")
