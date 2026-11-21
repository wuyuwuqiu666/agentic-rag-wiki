"""EmbeddingPort 加速换插位：云端点（受控网关）。默认不启用；B2 只对云调用记账。

凭证仅从 Settings（env）注入，禁字面量（T1）。M0 未接真实客户端，embed 显式 fail-loud。
"""

from __future__ import annotations

from src.platform.providers.errors import ProviderNotConfigured


class CloudEmbedding:
    def __init__(self, base_url: str | None, api_key: str | None, model_id: str = "cloud-embedding") -> None:
        if not (base_url and api_key):
            raise ProviderNotConfigured("cloud embedding 需 base_url + api_key（env 注入，T1）")
        self._base_url = base_url
        self._api_key = api_key
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return 1024

    @property
    def input_token_limit(self) -> int:
        return 8192

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderNotConfigured("云 embedding 客户端在 M1 装配表冻结后接入（当前仅占位端口）")
