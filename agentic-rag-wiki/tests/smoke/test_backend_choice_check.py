"""M4-ar §9/§5 后端选择护栏单测 + rerank=cloud 静默错配补漏验证。

- `backend_choice_check`：执法"ProviderChoices 每后端字段的非默认枚举值须在 composition.py 有显式分支"。
- 补漏：`c.rerank=="cloud"` 此前无分支（静默落 DevLexicalRerank），现经 CloudRerank 占位 fail-loud。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError
from src.platform.config.settings import ProviderChoices, Settings
from src.platform.harness import backend_choice_check as bcc
from src.platform.providers.errors import ProviderNotConfigured
from src.platform.providers.rerank_cloud import CloudRerank


def test_real_repo_clean() -> None:
    # 真实仓库：所有后端字段的非默认值均已显式分支（含本次补漏的 rerank=cloud）
    assert bcc.check() == []


def test_literal_values_parses_alias_domain() -> None:
    tree = ast.parse('X = Literal["a", "b", "c"]')
    sub = next(n.value for n in tree.body if isinstance(n, ast.Assign))
    assert bcc._literal_values(sub) == ["a", "b", "c"]


def test_default_value_is_exempt() -> None:
    # 默认档（else 兜底）无须显式出现：_backend_fields 正确解析域与默认值（默认值在 check 中豁免）
    tree = ast.parse("class ProviderChoices:\n    foo: Literal['cloud', 'local', 'dev'] = 'dev'\n")
    fields = bcc._backend_fields(tree)
    assert fields["foo"] == (["cloud", "local", "dev"], "dev")


def test_phantom_option_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 声明 "s3" 但 composition 无分支 → 检出幻影后端
    settings = tmp_path / "settings.py"
    settings.write_text(
        'Backend = Literal["fs", "s3"]\n'
        "class ProviderChoices:\n"
        "    objectstore: Backend = \"fs\"\n",
        encoding="utf-8",
    )
    composition = tmp_path / "composition.py"
    composition.write_text("x = 1  # 没有任何 c.objectstore == 分支\n", encoding="utf-8")
    monkeypatch.setattr(bcc, "_SETTINGS", settings)
    monkeypatch.setattr(bcc, "_COMPOSITION", composition)
    problems = bcc.check()
    assert any("s3" in p for p in problems)


def test_consumed_option_not_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 非默认值有显式分支 → 不误报
    settings = tmp_path / "settings.py"
    settings.write_text(
        'Backend = Literal["fs", "s3"]\n'
        "class ProviderChoices:\n"
        "    objectstore: Backend = \"fs\"\n",
        encoding="utf-8",
    )
    composition = tmp_path / "composition.py"
    composition.write_text('graph = S3() if c.objectstore == "s3" else FS()\n', encoding="utf-8")
    monkeypatch.setattr(bcc, "_SETTINGS", settings)
    monkeypatch.setattr(bcc, "_COMPOSITION", composition)
    assert bcc.check() == []


def test_rerank_cloud_no_longer_silent_dev() -> None:
    # 补漏实证①：选 rerank="cloud" 无凭证 → 配置解析即拒（E2 fail-fast，与 llm/embedding 云档同权；
    # M4-as 前此处漏检，凭证缺失要拖到组合根构造才暴雷）
    with pytest.raises(ValidationError) as ei:
        Settings(providers=ProviderChoices(rerank="cloud"))
    assert "CLOUD_RERANK_BASE_URL/CLOUD_RERANK_API_KEY" in str(ei.value.errors())
    # 补漏实证②：占位语义（镜像 CloudEmbedding）——缺凭证构造即拒；凭证齐则构造通过但调用显式 fail-loud，
    # 永不静默冒充真云 rerank；`c.rerank=="cloud"` 分支在位由本护栏对真实仓库 check()==[] 锁定。
    with pytest.raises(ProviderNotConfigured):
        CloudRerank(None, None)
    with pytest.raises(ProviderNotConfigured):
        CloudRerank("https://gw.example.com/v1", "k").rerank("q", [], 5)


def test_cloud_backend_missing_preflight_consumer_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 家族锁（M4-as）：新增可选 "cloud" 后端却漏扩出网预检 `_egress_offenders` → 护栏检出
    settings = tmp_path / "settings.py"
    settings.write_text(
        'Backend = Literal["cloud", "dev"]\n'
        "class ProviderChoices:\n"
        "    rerank: Backend = \"dev\"\n"
        "class Settings:\n"
        "    cloud_rerank_base_url: str | None = None\n"
        "    cloud_rerank_api_key: str | None = None\n"
        "    def _require_credentials_for_cloud(self) -> None:\n"
        "        if self.providers.rerank == \"cloud\" and not (\n"
        "            self.cloud_rerank_base_url and self.cloud_rerank_api_key\n"
        "        ):\n"
        "            raise ValueError()\n"
        "    def _egress_offenders(self) -> list:\n"
        "        return []  # 漏消费 cloud_rerank_base_url\n",
        encoding="utf-8",
    )
    composition = tmp_path / "composition.py"
    composition.write_text('r = Cloud() if c.rerank == "cloud" else Dev()\n', encoding="utf-8")
    monkeypatch.setattr(bcc, "_SETTINGS", settings)
    monkeypatch.setattr(bcc, "_COMPOSITION", composition)
    problems = bcc.check()
    assert any("_egress_offenders" in p and "cloud_rerank_base_url" in p for p in problems)


def test_cloud_backend_missing_credential_fields_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 可选 "cloud" 后端但 Settings 无对应凭证字段 → 预检无从落地，护栏同样检出
    settings = tmp_path / "settings.py"
    settings.write_text(
        'Backend = Literal["cloud", "dev"]\n'
        "class ProviderChoices:\n"
        "    rerank: Backend = \"dev\"\n"
        "class Settings:\n"
        "    pass\n",
        encoding="utf-8",
    )
    composition = tmp_path / "composition.py"
    composition.write_text('r = Cloud() if c.rerank == "cloud" else Dev()\n', encoding="utf-8")
    monkeypatch.setattr(bcc, "_SETTINGS", settings)
    monkeypatch.setattr(bcc, "_COMPOSITION", composition)
    problems = bcc.check()
    assert any("cloud_rerank_base_url" in p for p in problems)
    assert any("_require_credentials_for_cloud" in p for p in problems)
