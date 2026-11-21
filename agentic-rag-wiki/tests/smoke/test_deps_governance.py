"""E8 依赖治理离线单测：许可证分类口径 + SBOM diff + 门禁退出码（纯逻辑，不依赖网络/装包）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.platform.harness import deps_governance as dg


@pytest.mark.parametrize(
    ("lic", "want"),
    [
        ("MIT", "allowed"),
        ("Apache-2.0", "allowed"),
        ("BSD-3-Clause", "allowed"),
        ("License :: OSI Approved :: MIT License", "allowed"),
        ("GPL-3.0-only", "denied"),
        ("GNU Affero General Public License v3", "denied"),
        ("LGPL-2.1", "denied"),
        ("Proprietary", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_classify_licenses(lic: str | None, want: str) -> None:
    assert dg.classify(lic) == want


def test_classify_copyleft_wins_over_permissive_substring() -> None:
    # 同时含 mit 与 gpl 的病态串按保守=被禁（先判 deny）
    assert dg.classify("MIT + GPL dual") == "denied"


@pytest.mark.parametrize(
    ("req", "name"),
    [("pydantic>=2.7", "pydantic"), ("psycopg[binary]>=3.1", "psycopg"), ("types-PyYAML", "types-PyYAML"), (" torch>=2.11 ", "torch")],
)
def test_normalize_requirement_name(req: str, name: str) -> None:
    assert dg._normalize_name(req) == name


def test_sbom_diff_added_removed_changed() -> None:
    base = [{"name": "a", "license": "MIT"}, {"name": "b", "license": "MIT"}]
    cur = [{"name": "b", "license": "Apache-2.0"}, {"name": "c", "license": "MIT"}]
    d = dg.diff(base, cur)
    assert d == {"added": ["c"], "removed": ["a"], "changed": ["b"]}


def test_main_passes_on_real_core_deps(tmp_path: Path) -> None:
    out = tmp_path / "sbom.json"
    rc = dg.main(["--pyproject", "pyproject.toml", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["components"]  # 至少 pydantic/pydantic-settings/PyYAML
    assert all(c["status"] in ("allowed", "unknown") for c in payload["components"])  # 无 denied


def _patch_sbom(monkeypatch: pytest.MonkeyPatch, entries: list[dict[str, str]]) -> None:
    monkeypatch.setattr(dg, "build_sbom", lambda reqs: (entries, []))


def test_main_denied_returns_nonzero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_sbom(monkeypatch, [{"name": "evil", "version": "1", "license": "GPL-3.0", "status": "denied"}])
    assert dg.main(["--out", str(tmp_path / "s.json")]) == 1


def test_main_unknown_advisory_zero_but_strict_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_sbom(monkeypatch, [{"name": "weird", "version": "1", "license": "Proprietary", "status": "unknown"}])
    assert dg.main(["--out", str(tmp_path / "a.json")]) == 0  # 默认仅告警
    assert dg.main(["--out", str(tmp_path / "b.json"), "--strict"]) == 1  # strict 翻红


def test_main_baseline_diff_prints(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = tmp_path / "baseline.json"
    base.write_text(json.dumps({"components": [{"name": "old", "license": "MIT"}]}), encoding="utf-8")
    _patch_sbom(monkeypatch, [{"name": "pydantic", "version": "2", "license": "MIT", "status": "allowed"}])
    rc = dg.main(["--out", str(tmp_path / "s.json"), "--baseline", str(base)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "added" in printed and "removed" in printed
