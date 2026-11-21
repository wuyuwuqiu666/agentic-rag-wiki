"""LlmPort 本地换载档：3B GGUF（llama.cpp/ollama），按需加载，不常驻。

embed+rerank 常驻约 3.2GB，无 LLM 常驻位；B3 断云时按域强制换载此档（R11）。
"""

from __future__ import annotations

from src.platform.providers.errors import ProviderNotConfigured


class LocalLlm:
    def __init__(self, model_path: str | None = None) -> None:
        self._model_path = model_path

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        raise ProviderNotConfigured("本地 3B 换载档在 M4 B3 演练接入（当前仅占位端口）")
