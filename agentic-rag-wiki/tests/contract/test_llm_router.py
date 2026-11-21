"""FR-ASK-4 双端点路由验收：主挂备接不抛、全挂才抛（→上层 B3）、灰度确定性分流、on_fail 上报。"""

from __future__ import annotations

import pytest
from src.platform.providers.errors import ProviderError
from src.platform.providers.llm_router import LlmRouter


class _Ok:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.calls = 0

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        self.calls += 1
        return f"{self.tag}:{prompt[:4]}"


class _Boom:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.calls = 0

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        self.calls += 1
        raise ProviderError(f"{self.tag} 不可达")


def test_primary_failure_auto_switches_to_local_fallback() -> None:
    reported: list[str] = []
    primary, fallback = _Boom("cloud"), _Ok("local")
    router = LlmRouter(primary, fallback=fallback, on_fail=lambda ep, exc: reported.append(ep))
    out = router.complete("问题X")
    assert out.startswith("local:")  # B3 断云→自动切本地兜底
    assert reported == ["primary"]  # 主档失败上报，但未向上抛（不触发 B3 硬断）


def test_full_outage_raises_for_upper_b3() -> None:
    reported: list[str] = []
    router = LlmRouter(_Boom("cloud"), fallback=_Boom("local"), on_fail=lambda ep, exc: reported.append(ep))
    with pytest.raises(ProviderError):  # 主+备皆挂→抛出，交由 App fail-loud→B3
        router.complete("问题X")
    assert reported == ["primary", "fallback"]


def test_no_fallback_raises_immediately() -> None:
    with pytest.raises(ProviderError):
        LlmRouter(_Boom("cloud")).complete("问题X")


def test_canary_grayscale_routes_deterministically() -> None:
    primary, canary = _Ok("p"), _Ok("c")
    all_canary = LlmRouter(primary, canary=canary, canary_ratio=100)
    assert all_canary.complete("问1").startswith("c:")  # ratio=100 全量走 canary
    assert primary.calls == 0

    none_canary = LlmRouter(primary, canary=_Ok("c"), canary_ratio=0)
    assert none_canary.complete("问2").startswith("p:")  # ratio=0 不灰度
    assert canary.calls == 1  # 仍只被上一台 all_canary 调用过一次


def test_canary_failure_falls_back_to_local() -> None:
    router = LlmRouter(_Ok("p"), fallback=_Ok("local"), canary=_Boom("c"), canary_ratio=100)
    assert router.complete("问题X").startswith("local:")  # canary 挂→兜底接上，不抛
