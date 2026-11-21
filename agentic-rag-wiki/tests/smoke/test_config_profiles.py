"""E2 环境矩阵快照测试：dev/staging/prod 只差 config，代码零分支；选云档缺凭证=fail-fast 启动失败。

PRD §15 E2 机检产物="config/ 三 profile 快照测试（缺 env 即启动失败）"。
确定性：直接构造 Settings(overrides)，不读宿主 env（lru_cache 的 get_settings 也不在此触，避免串扰）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from src.platform.config.settings import Settings


def _missing(exc: pytest.raises) -> str:
    err = exc.value.errors()[0]
    return str(err["ctx"]["missing"])


def test_three_envs_share_one_class_and_defaults() -> None:
    """三档只是 env 字段取值不同，其余默认一致=代码零分支差异（E2 核心不变式）。"""
    snaps = [Settings(env=e) for e in ("dev", "staging", "prod")]
    for s in snaps:
        assert s.shadow_breaker_set == {"B1", "B5"}
        assert s.providers.embedding == "dev_hash"  # M0 可跑通档，逐端口默认一致
    # 除 env 外，三档 model_dump 完全相同（无 if env==prod 的配置分叉）
    base = snaps[0].model_dump(exclude={"env"})
    for s in snaps[1:]:
        assert s.model_dump(exclude={"env"}) == base


def test_cloud_choice_without_credentials_fails_fast() -> None:
    """选 llm=cloud 却没配凭证 → 启动即抛，绝不静默回落 dev（T1/NFR-8 精神）。"""
    with pytest.raises(ValidationError) as exc:
        Settings(env="prod", providers={"llm": "cloud"})
    assert "CLOUD_LLM_BASE_URL/CLOUD_LLM_API_KEY" in _missing(exc)


def test_cloud_choice_with_credentials_loads() -> None:
    s = Settings(
        env="prod",
        providers={"llm": "cloud"},
        cloud_llm_base_url="https://gateway.internal/v1",
        cloud_llm_api_key="from-env-not-committed",
    )
    assert s.providers.llm == "cloud"
    assert s.env == "prod"


def test_embedding_cloud_requires_its_own_credentials() -> None:
    """逐端口独立：embedding=cloud 缺自己那对凭证也要 fail-fast（禁全局一刀切掩盖）。"""
    with pytest.raises(ValidationError) as exc:
        Settings(env="staging", providers={"embedding": "cloud"})
    assert "CLOUD_EMBEDDING" in _missing(exc)
