"""lifecycle_check 自测（§9 产物 #4a、E8 审计保留期机检）：真实仓库全绿 + 各违规路可检出。

同 param_class_check/schemas.validate 教训——护栏自身须有回归，否则被静默清空/削弱无人拦。
构造性用例经 monkeypatch `load` 注入畸形 cfg（不写真实 yaml），断言每类违规都出得来。
"""

from __future__ import annotations

import types

import pytest
from src.platform.harness import lifecycle_check as lc


def _valid_cfg() -> dict:
    return {
        "retention": {
            "workspace/agent-scratch": {"max_age_days": 3, "action": "delete"},
            "workspace/eval": {"max_age_days": 180, "action": "archive"},
        },
        "protected_append_only": ["workspace/audit", "tests/eval"],
    }


def test_real_repo_check_clean():
    assert lc.check() == []


def test_real_repo_main_zero():
    assert lc.main() == 0


def test_bad_max_age_days(monkeypatch):
    cfg = _valid_cfg()
    cfg["retention"]["workspace/agent-scratch"]["max_age_days"] = 0
    monkeypatch.setattr(lc, "load", lambda *a, **k: cfg)
    assert any("max_age_days" in p for p in lc.check())


def test_bad_action(monkeypatch):
    cfg = _valid_cfg()
    cfg["retention"]["workspace/eval"]["action"] = "purge"
    monkeypatch.setattr(lc, "load", lambda *a, **k: cfg)
    assert any("action" in p for p in lc.check())


def test_empty_protected(monkeypatch):
    cfg = _valid_cfg()
    cfg["protected_append_only"] = []
    monkeypatch.setattr(lc, "load", lambda *a, **k: cfg)
    assert any("protected_append_only" in p for p in lc.check())


def test_conflict_protection_vs_retention(monkeypatch):
    cfg = _valid_cfg()
    # 审计既受保护又被列入自动删保留 → 数据丢失风险，必红
    cfg["retention"]["workspace/audit"] = {"max_age_days": 30, "action": "delete"}
    monkeypatch.setattr(lc, "load", lambda *a, **k: cfg)
    assert any("冲突" in p and "workspace/audit" in p for p in lc.check())


def test_missing_append_only_store(monkeypatch):
    cfg = _valid_cfg()
    cfg["protected_append_only"] = ["tests/eval"]  # 丢审计保护
    monkeypatch.setattr(lc, "load", lambda *a, **k: cfg)
    assert any("E8 保留期未显式配置" in p and "workspace/audit" in p for p in lc.check())


def test_drift_audit_dir_unprotected(monkeypatch):
    cfg = _valid_cfg()
    monkeypatch.setattr(lc, "load", lambda *a, **k: cfg)
    # 审计实际落点改到未受保护路径 → 漂移防护必红（即便 yaml 自身仍写着 workspace/audit）
    fake = types.SimpleNamespace(model_fields={"audit_dir": types.SimpleNamespace(default="data/audit")})
    monkeypatch.setattr(lc, "Settings", fake)
    assert any("Settings.audit_dir" in p for p in lc.check())


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("workspace/audit", True),
        ("workspace/audit/2026-09.jsonl", True),
        ("tests/eval/golden.jsonl", True),
        ("workspace/agent-scratch/tmp", False),
        ("workspace/auditx", False),  # 前缀边界：非 audit 子目录不误判受保护
    ],
)
def test_is_protected_prefix(path, expected):
    assert lc.is_protected(path, cfg=_valid_cfg()) is expected
