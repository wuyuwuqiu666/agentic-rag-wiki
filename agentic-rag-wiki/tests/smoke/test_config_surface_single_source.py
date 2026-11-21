"""M4-al §10/T1 配置面单一口径：把 `.env.example` 里两条"声明却零消费"的幻影键转为真实消费者。

离线零外部依赖（不下载权重、不出网）：
- `APP_EGRESS_ALLOWLIST_HOSTS` → 应用侧装配预检（`_require_credentials_for_cloud`/`egress_host_allowed`）：
  选定云档时其 base_url 主机须在白名单内，否则启动即 fail-fast（网络层真·防火墙=放行项⑥，本机不伪造）。
- `APP_LOCAL_EMBEDDING_MODEL_DIR` → `LocalEmbedding.model_dir`（构造即存，`_load` 传给 cache_folder）；
  权重落盘/ModelScope 下载=放行项①，本测试只锁"配置→构造参数"这条贯通缝，不触依赖。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from src.platform.config.settings import ProviderChoices, Settings, egress_host_allowed, host_of, hosts_of
from src.platform.providers.embedding_local import LocalEmbedding


# ---------- 主机名规范化（出网白名单唯一口径源） ----------
def test_host_of_strips_scheme_path_port_case() -> None:
    assert host_of("https://GW.Example.com/v1/chat") == "gw.example.com"
    assert host_of("http://gw.example.com:8443") == "gw.example.com"
    assert host_of("gw.example.com") == "gw.example.com"


def test_hosts_of_and_egress_membership() -> None:
    assert hosts_of("a.com, B.com ,,") == {"a.com", "b.com"}
    assert egress_host_allowed("https://b.com/v1", "a.com,b.com") is True
    assert egress_host_allowed("https://evil.com/v1", "a.com,b.com") is False
    # 空白名单=dev 档不启用预检，一律放行
    assert egress_host_allowed("https://anything.com", "") is True


# ---------- 装配预检 fail-fast（幻影键转真实消费者） ----------
def test_cloud_host_off_allowlist_fails_fast() -> None:
    with pytest.raises(ValidationError, match="云档出网主机越界"):
        Settings(
            providers=ProviderChoices(llm="cloud"),
            cloud_llm_base_url="https://evil.example.com/v1",
            cloud_llm_api_key="k",
            egress_allowlist_hosts="gw.internal-llm.example.com",
        )


def test_cloud_host_on_allowlist_passes() -> None:
    s = Settings(
        providers=ProviderChoices(llm="cloud"),
        cloud_llm_base_url="https://gw.internal-llm.example.com/v1",
        cloud_llm_api_key="k",
        egress_allowlist_hosts="gw.internal-llm.example.com",
    )
    assert s.egress_allowlist_hosts == "gw.internal-llm.example.com"


def test_cloud_rerank_host_off_allowlist_fails_fast() -> None:
    # 云档预检覆盖面 = 所有选定云档（llm/embedding/rerank），漏一个即"声称管全部、实则放行一个"
    with pytest.raises(ValidationError, match="云档出网主机越界"):
        Settings(
            providers=ProviderChoices(rerank="cloud"),
            cloud_rerank_base_url="https://evil.example.com/v1",
            cloud_rerank_api_key="k",
            egress_allowlist_hosts="gw.internal.example.com",
        )


def test_empty_allowlist_keeps_dev_path_unchanged() -> None:
    # dev/放行档：白名单空 → 云档只校验凭证、不做主机预检（承既有默认路径）
    s = Settings(
        providers=ProviderChoices(llm="cloud"),
        cloud_llm_base_url="https://any.example.com/v1",
        cloud_llm_api_key="k",
    )
    assert s.cloud_llm_base_url is not None


# ---------- 权重目录贯通（不触 sentence-transformers 依赖） ----------
def test_model_dir_flows_into_local_embedding() -> None:
    emb = LocalEmbedding("qwen3-embedding-0.6b", model_dir="D:/models/qwen3")
    assert emb.model_dir == "D:/models/qwen3"
    assert LocalEmbedding().model_dir is None  # 缺省回退框架默认缓存，行为不变


def test_settings_model_dir_default_none() -> None:
    assert Settings().local_embedding_model_dir is None
    assert Settings(local_embedding_model_dir="/w").local_embedding_model_dir == "/w"
