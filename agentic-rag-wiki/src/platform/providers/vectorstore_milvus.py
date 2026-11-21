"""VectorStorePort 生产档：Milvus（pymilvus）。M0 仅登记端口，需真实集群方可实例化。

装配表默认 memory；M1 契约测试在提供 MILVUS_URI 时对本实现跑同一套双实现用例。
"""

from __future__ import annotations

from src.platform.providers.errors import ProviderNotConfigured
from src.platform.types import Chunk


class MilvusVectorStore:
    def __init__(self, uri: str | None) -> None:
        if not uri:
            raise ProviderNotConfigured("milvus 需 MILVUS_URI（M1 接入）")
        try:
            import pymilvus  # noqa: F401, PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ProviderNotConfigured("milvus 档需 pymilvus（pip install .[milvus]）") from exc
        self._uri = uri

    def upsert(self, chunks: list[Chunk]) -> int:
        raise ProviderNotConfigured("Milvus 实现在 M1 装配表冻结后接入")

    def delete(self, chunk_ids: list[str]) -> int:
        raise ProviderNotConfigured("Milvus 实现在 M1 装配表冻结后接入")

    def search(self, vector: list[float], topk: int, acl_tags: list[str]) -> list[Chunk]:
        raise ProviderNotConfigured("Milvus 实现在 M1 装配表冻结后接入")

    def alias_swap(self, old: str, new: str) -> None:
        raise ProviderNotConfigured("Milvus 别名切换在 M1 接入（高危：双别名保留 72h）")

    def all_ids(self) -> list[str]:
        raise ProviderNotConfigured("Milvus 全量清单枚举在 M1 接入（对账 B5 依赖）")
