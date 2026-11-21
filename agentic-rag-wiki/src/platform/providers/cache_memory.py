"""CachePort 内存实现（dev 档）：进程内字典，精确键命中；企业档=向量相似召回，键/准入不变（G5）。

存储层不含安全判定——键由 service/ask/cache 生成（首段恒为 ACL 域指纹），故跨域天然不同键、不可命中。
"""

from __future__ import annotations

from src.platform.types import Answer


class MemoryCache:
    def __init__(self) -> None:
        self._by_key: dict[str, Answer] = {}

    def get(self, key: str) -> Answer | None:
        return self._by_key.get(key)

    def put(self, key: str, value: Answer) -> None:
        self._by_key[key] = value

    def invalidate_domain(self, domain: str) -> int:
        """FR-ING-5③：按 CachePort 键约定（首段=逗号连接的 ACL 域指纹）剔除命中该域的条目。"""
        dropped = 0
        for key in list(self._by_key):
            fingerprint = key.split("::", 1)[0]
            if domain in fingerprint.split(","):
                del self._by_key[key]
                dropped += 1
        return dropped
