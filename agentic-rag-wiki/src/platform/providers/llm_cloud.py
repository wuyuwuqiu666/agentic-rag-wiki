"""LlmPort 执行档主档：云端受控网关（唯一出网点，R11）。凭证 env 注入（T1）。

M0 未接真实客户端，complete 显式 fail-loud；B3 断云时按域换载 llm_local。
"""

from __future__ import annotations

from src.platform.providers.errors import ProviderNotConfigured


class CloudLlm:
    def __init__(self, base_url: str | None, api_key: str | None, model_id: str = "cloud-llm") -> None:
        if not (base_url and api_key):
            raise ProviderNotConfigured("cloud llm 需 base_url + api_key（env 注入，T1）")
        self._base_url = base_url
        self._api_key = api_key
        self._model_id = model_id

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        raise ProviderNotConfigured("云 LLM 客户端在 M2 装配表接入后启用（当前仅占位端口）")
