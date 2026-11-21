"""RerankPort 加速换插位：云端点（受控网关）。默认不启用；凭证仅从 Settings（env）注入，禁字面量（T1）。

此前 `RerankBackend=Literal["local","cloud","dev_lexical"]` 的 "cloud" 档在组合根**无分支**（只判 local），
选 `rerank="cloud"` 静默落到 `DevLexicalRerank`（以为接了云 rerank 实拿 dev 档=同 pgvector/langgraph/s3/kafka
静默错配危险类，M4-ar 补漏）。本占位镜像 `CloudEmbedding`：缺 base_url/api_key 即 `ProviderNotConfigured`，
`rerank` 显式 fail-loud（云 rerank 客户端在装配表冻结 + 企业放行项就位后接入，本 agent 不伪造）。
"""

from __future__ import annotations

from src.platform.providers.errors import ProviderNotConfigured
from src.platform.types import Chunk


class CloudRerank:
    def __init__(self, base_url: str | None, api_key: str | None, model_id: str = "cloud-rerank") -> None:
        if not (base_url and api_key):
            raise ProviderNotConfigured("cloud rerank 需 base_url + api_key（env 注入，T1）")
        self._base_url = base_url
        self._api_key = api_key
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def rerank(self, query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]:
        raise ProviderNotConfigured("云 rerank 客户端在 M1 装配表冻结 + 企业放行项接入（当前仅占位端口）")
