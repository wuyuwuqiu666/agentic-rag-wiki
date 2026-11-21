"""M4-aa：E8/T1 凭证扫描门禁单一源（harness.secret_scan）离线单测。

detect-secrets 是可选工具（本机不装），但**判定口径**是纯函数——此处锁死"命中即拦、无命中放行、
输出漂移不静默通过"，正是修 hooks/pre-commit 旧 `scan --baseline .secrets.baseline`（缺文件即崩 +
scan --baseline=刷新基线永不拦）"查得到≠拦得住"盲点的回归守卫。
"""

from __future__ import annotations

import io
import json

import pytest
from src.platform.harness import secret_scan


def test_clean_scan_passes() -> None:
    secret_scan.assert_no_findings({"results": {}})  # 无扫描面
    secret_scan.assert_no_findings({"results": {"a.py": [], "b.env": []}})  # 各文件零命中


def test_finding_blocks() -> None:
    with pytest.raises(ValueError):
        secret_scan.assert_no_findings({"results": {"a.py": [], "leak.py": [{"type": "Secret", "line": "AWS_KEY=x"}]}})


def test_malformed_output_fails_loud() -> None:
    """输出结构漂移不得静默放行（NFR-8 精神）。"""
    with pytest.raises(ValueError):
        secret_scan.assert_no_findings({})  # 缺 results
    with pytest.raises(ValueError):
        secret_scan.assert_no_findings({"results": "not-a-dict"})  # 类型不符


def test_main_stdin_clean_exit_zero(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"results": {"a.py": []}})))
    assert secret_scan.main([]) == 0


def test_main_stdin_finding_exit_one(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"results": {"leak.py": [{"type": "Secret"}]}})))
    assert secret_scan.main([]) == 1


def test_main_stdin_unparseable_exit_one(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    assert secret_scan.main([]) == 1
