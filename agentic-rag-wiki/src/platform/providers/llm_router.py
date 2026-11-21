"""LlmPort 双端点路由（FR-ASK-4）：同一端口下按 config 选主档，主档故障自动切本地兜底。

- 灰度：`canary` 端点按 `canary_ratio`（0..100）确定性计数分流（每 100 次里前 ratio 次走 canary）。
- 韧性：主/canary 抛 `ProviderError` → 回退 `fallback`（B3 断云时切本地，R11）；per-endpoint 失败经 `on_fail`
  回调上报（组合根注入=审计"主档失败已兜底"，不判 B3）。**仅当兜底也失败**才向外抛 `ProviderError`→上层 fail-loud→B3。
路由/回退是编排，零 SDK；具体端点实现在各自 `llm_<impl>.py`。
"""

from __future__ import annotations

from collections.abc import Callable

from src.platform.providers.errors import ProviderError
from src.platform.providers.ports import LlmPort


class LlmRouter:
    def __init__(
        self,
        primary: LlmPort,
        *,
        fallback: LlmPort | None = None,
        canary: LlmPort | None = None,
        canary_ratio: int = 0,
        on_fail: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._canary = canary
        self._ratio = max(0, min(100, canary_ratio))
        self._on_fail = on_fail
        self._seq = 0

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        self._seq += 1
        head = self._pick()
        label = "canary" if head is self._canary else "primary"
        try:
            return head.complete(prompt, max_tokens)
        except ProviderError as exc:
            self._report(label, exc)
            return self._with_fallback(prompt, max_tokens, first_error=exc)

    def _pick(self) -> LlmPort:
        if self._canary is not None and self._ratio > 0 and (self._seq % 100) < self._ratio:
            return self._canary
        return self._primary

    def _with_fallback(self, prompt: str, max_tokens: int, *, first_error: Exception) -> str:
        if self._fallback is None:
            raise first_error  # 无兜底档=供给真断，交由上层 B3 fail-loud
        try:
            return self._fallback.complete(prompt, max_tokens)
        except ProviderError as exc:
            self._report("fallback", exc)
            raise  # 主+备皆挂→向外抛，App 触发 B3

    def _report(self, endpoint: str, exc: Exception) -> None:
        if self._on_fail is not None:
            self._on_fail(endpoint, exc)
