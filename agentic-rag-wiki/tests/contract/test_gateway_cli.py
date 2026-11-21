"""M4-bm gateway CLI 命令面契约：§5 line 93 "chat+wiki+admin API/CLI" 的 CLI 半边落点验证。

补此前零入口文件的幻影产物——组合根 façade（M4-x）之上的人可敲命令行。断言口径：
①各子命令把既有 façade 结果序列化成可解析 JSON；②双人复位门禁经命令面仍强制（异常→非零退出 + stderr 可读，不 traceback 崩台）；
③一次端到端运维回路（危险批量删除触 B0→CLI 复位→CLI 回放 breaker_reset）全经命令行可达。零新依赖/不出网/不绑端口。
"""

from __future__ import annotations

import json

import pytest
from src.platform.config import Settings
from src.platform.gateway import cli
from src.platform.gateway.composition import Container, build_container
from src.platform.providers.errors import BreakerTripped
from src.platform.types import ChangeEvent, Document


def _container(tmp_path) -> Container:
    return build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), shadow_breakers=""))


def _feed_hr_doc(c: Container) -> None:
    c.ingest.process_document(
        Document(doc_id="d1", version=1, domain="hr", acl_tags=["hr"], raw_text="# 考勤\n\n" + "\n\n".join(f"第{i}条规定。" for i in range(30)))
    )


def test_cli_ask_emits_parsable_answer_json(tmp_path, capsys) -> None:
    c = _container(tmp_path)
    _feed_hr_doc(c)
    rc = cli.main(["ask", "考勤怎么规定", "--acl", "hr"], container=c)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) >= {"question", "text", "citations", "refused"}


def test_cli_admin_reads_emit_json(tmp_path, capsys) -> None:
    c = _container(tmp_path)
    for argv, kind in (
        (["admin", "pool"], dict),
        (["admin", "funnel"], dict),
        (["admin", "audit-replay"], list),
        (["admin", "degrade-plan", "--total", "100", "--bad", "5"], dict),
    ):
        assert cli.main(argv, container=c) == 0
        assert isinstance(json.loads(capsys.readouterr().out), kind)


def test_cli_degrade_plan_carries_kernel_fields(tmp_path, capsys) -> None:
    c = _container(tmp_path)
    assert cli.main(["admin", "degrade-plan", "--total", "1000", "--bad", "20"], container=c) == 0
    plan = json.loads(capsys.readouterr().out)
    assert set(plan) == {"level", "exhausted", "reason"}


def test_cli_reset_rejects_same_signer(tmp_path, capsys) -> None:
    """命令面不吞双人校验：同一人签署→façade 抛 ValueError→CLI 非零退出且 stderr 可读（fail-loud 非 traceback）。"""
    c = _container(tmp_path)
    rc = cli.main(["admin", "reset", "--breaker", "B1", "--operator", "sre-a", "--approver", "sre-a", "--reason", "处置"], container=c)
    assert rc == 1
    err = capsys.readouterr().err
    assert "双人复位" in err and "ValueError" in err


def test_cli_ops_loop_trip_reset_replay(tmp_path, capsys) -> None:
    """端到端运维回路全经命令行：危险批量删除触硬断→CLI 复位恢复→CLI 回放 breaker_reset（U4+§10 经 CLI 可达）。"""
    c = _container(tmp_path)
    _feed_hr_doc(c)
    with pytest.raises(BreakerTripped):  # B1 批量删除硬断拉闸（test_admin_surface 同口径）
        c.ingest.apply_changes([ChangeEvent(doc_id=f"x{i}", kind="delete") for i in range(5)])
    assert cli.main(["admin", "reset", "--breaker", "B1", "--operator", "sre-a", "--approver", "sre-b", "--reason", "已切本地供给"], container=c) == 0
    capsys.readouterr()
    assert cli.main(["admin", "audit-replay", "--kind", "breaker_reset"], container=c) == 0
    events = json.loads(capsys.readouterr().out)
    assert events and all(e["kind"] == "breaker_reset" for e in events)
