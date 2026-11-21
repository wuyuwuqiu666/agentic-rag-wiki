"""M4-ao §9 配置面单一口径护栏自测：模板键 ⊆ Settings 字段不变式（防"幻影配置"回归）。

同 lifecycle_check/param_class_check 口径：既验真实仓库全绿（当前无漂移），也 monkeypatch 检出幻影键——
否则"护栏自身可被静默削弱而无回归拦截"（M4-w 审计线教训）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from src.platform.harness import config_surface_check as csc


def test_real_repo_has_no_phantom_config() -> None:
    assert csc.check() == []


def test_to_field_mapping() -> None:
    assert csc.to_field("APP_LOG_LEVEL") == "log_level"
    assert csc.to_field("APP_PROVIDERS__LLM") == "providers__llm"
    assert csc.to_field("APP_LOCAL_EMBEDDING_MODEL_DIR") == "local_embedding_model_dir"


def test_settings_fields_include_nested_provider_choices() -> None:
    fields = csc._settings_fields(csc._SETTINGS)
    assert "log_level" in fields  # Settings 顶层
    assert "providers__llm" in fields  # ProviderChoices 嵌套映射


def test_env_keys_captures_commented_template_too() -> None:
    keys = dict((k, c) for k, c in csc._env_keys(csc._ENV_EXAMPLE))
    assert "APP_LOG_LEVEL" in keys  # 未注释
    assert "APP_PROVIDERS__LLM" in keys  # 注释样板键同样入检
    assert keys["APP_PROVIDERS__LLM"] == "#"


def test_phantom_key_detected(monkeypatch, tmp_path: Path) -> None:
    # 模板广告 APP_NOT_A_REAL_FIELD= 但 Settings 无 → check() 报漂移
    env = tmp_path / ".env.example"
    env.write_text("APP_LOG_LEVEL=INFO\nAPP_NOT_A_REAL_FIELD=x\n", encoding="utf-8")
    monkeypatch.setattr(csc, "_ENV_EXAMPLE", env)
    problems = csc.check()
    assert any("APP_NOT_A_REAL_FIELD" in p for p in problems)
    assert not any("APP_LOG_LEVEL" in p for p in problems)  # 合法键不误报


def test_valid_key_not_flagged(monkeypatch, tmp_path: Path) -> None:
    env = tmp_path / ".env.example"
    env.write_text("APP_LOG_LEVEL=INFO\nAPP_EGRESS_ALLOWLIST_HOSTS=\n", encoding="utf-8")
    monkeypatch.setattr(csc, "_ENV_EXAMPLE", env)
    assert csc.check() == []


# ---------- M4-au 反向半边：字段⇒必有消费者（幻影旋钮防回归） ----------
def test_phantom_field_detected_and_exempt_not_overflag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src"
    cfg = src / "platform" / "config"
    cfg.mkdir(parents=True)
    settings = cfg / "settings.py"
    settings.write_text(
        "class ProviderChoices:\n"
        "    pass\n"
        "class Settings:\n"
        "    env: str = \"dev\"\n"       # E2 零分支 profile 标签：豁免在册
        "    phantom_knob: int = 7\n"    # 全 src 零读取：应检出
        "    consumed_knob: int = 1\n",  # 被 consumer.py 属性读取：不误报
        encoding="utf-8",
    )
    (src / "consumer.py").write_text("def f(s):\n    return s.consumed_knob\n", encoding="utf-8")
    monkeypatch.setattr(csc, "_SRC", src)
    monkeypatch.setattr(csc, "_SETTINGS", settings)
    problems = csc._phantom_field_problems()
    assert any("phantom_knob" in p for p in problems)
    assert not any("env" in p or "consumed_knob" in p for p in problems)


def test_exempt_entries_stay_live() -> None:
    # 豁免表防腐：每条豁免必须仍是真实字段（字段被删则豁免同步删，防"僵尸豁免"吞未来幻影）且有理由文案
    tree = ast.parse(csc._SETTINGS.read_text(encoding="utf-8"))
    names, _ = csc._declared_fields(tree)
    assert set(csc._EXEMPT_FIELDS) <= names
    assert all(csc._EXEMPT_FIELDS.values())
