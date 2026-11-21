"""M4-am §10/NFR-8 配置面单一口径续：`log_level` 与 `local_input_token_limit` 两枚幻影旋钮转真实消费者。

离线零外部依赖（不启真·日志聚合、不下载权重）：
- `log_level` → `resolve_log_level`（纯映射，非法级别 fail-loud）+ `configure_logging`（装配引导，stdlib logging）。
- `local_input_token_limit` → `LocalEmbedding.input_token_limit`（NFR-8 门限唯一口径源，pipeline 据此拦超限块）；
  composition 注入令"配置改→门限改"贯通（真机 tokenizer 计数=放行档，本环只锁口径接线不伪造）。
"""

from __future__ import annotations

import logging

import pytest
from src.platform.config.settings import ProviderChoices, Settings, configure_logging, resolve_log_level
from src.platform.gateway.composition import build_container
from src.platform.providers.embedding_local import LocalEmbedding


# ---------- log_level 真实消费者 ----------
def test_resolve_log_level_valid_and_case_insensitive() -> None:
    assert resolve_log_level("INFO") == logging.INFO
    assert resolve_log_level(" debug ") == logging.DEBUG
    assert resolve_log_level("Warning") == logging.WARNING


def test_resolve_log_level_rejects_invalid() -> None:
    # 拼写错/未知别名不得静默回退 INFO（否则运维预期与实际背离，NFR-8 同口径）
    for bad in ("DEBUGG", "VERBOSE", "", "not-a-level"):
        with pytest.raises(ValueError):
            resolve_log_level(bad)


def test_configure_logging_returns_resolved_level() -> None:
    assert configure_logging("ERROR") == logging.ERROR
    with pytest.raises(ValueError):
        configure_logging("NOPE")


# ---------- local_input_token_limit 真实消费者 ----------
def test_local_input_token_limit_flows_into_provider() -> None:
    assert LocalEmbedding().input_token_limit == 32768  # ADR-004 默认档
    assert LocalEmbedding(input_token_limit=4096).input_token_limit == 4096  # 配置改→门限改


def test_composition_injects_settings_input_limit() -> None:
    # 组合根把 Settings.local_input_token_limit 注入本地 embedding（不触 sentence-transformers：仅构造不 _load）
    s = Settings(providers=ProviderChoices(embedding="local"), local_input_token_limit=1234)
    container = build_container(s)
    assert container.embedding.input_token_limit == 1234


def test_default_local_input_limit_matches_provider_default() -> None:
    # 单一口径不变式：Settings 默认 == LocalEmbedding 默认，两处 32768 不得静默漂移
    assert Settings().local_input_token_limit == LocalEmbedding().input_token_limit
