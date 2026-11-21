"""M4-cm 索引重建/别名切回的**写侧可达入口**契约：HTTP `POST /api/ingest/{reindex,alias/revert}` + CLI `admin {reindex,alias-revert}`。

此前 `shadow_rebuild()`/`revert_alias()` 只有代码与 smoke 锁（在册缺口＝M4-cm），本刀把 §10「一键切回旧别名」
与 FR-ING-3 重建给到可达面，并锁六条不变式：
①**门面零判定**——切换/回滚的控制流只在 `service/reindex`，三个面（HTTP／CLI／程序化）拿同一个 `ReindexOutcome`；
②**confirm 是门不是装饰**——缺省、false、CLI 没敲 `--confirm` 一律拒，拒的时刻别名一次也没切；
③**B0 总闸判门在全量重放之前**（本刀同时补齐这条门，且管线判的就是组合根那一册闸）——冻结期 503/退出码 1
  且零部分效果；回执点名"写侧已拉闸（B0）"，不把总闸说成单类闸也不反过来；
④**切换失败是设计内的失败形态**——`applied=false` ∧ `rolled_back=true` 给 200 如实读数，不升格成 5xx；
⑤**双失败不静默**——回滚也失败＝别名可能悬空，`ProviderError` 原样上抛（CLI 退出码 1），不返回"看起来切完了"；
⑥**同名不触库**——`from_alias==to_alias` 在请求边界 422（CLI 退出码 1）；执行档不核验 72h 保留窗，如实写进回执。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from src.platform.config import Settings
from src.platform.gateway.cli import main
from src.platform.gateway.composition import Container, build_container
from src.platform.gateway.http import _create_app
from src.platform.providers.errors import BreakerTripped, ProviderError

_KEYS = ("applied", "rolled_back", "active_alias", "error")


def _ctr(tmp_path, *, hard: bool = False) -> Container:
    # 缺省＝里程碑观察档（B1/B5 只记不拦）；`hard=True` 才谈得上"未复位硬断累积升 B0"。
    return build_container(
        Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), **({"shadow_breakers": ""} if hard else {}))
    )


def _wired(tmp_path, *, hard: bool = False) -> tuple[TestClient, Container]:
    ctr = _ctr(tmp_path, hard=hard)
    return TestClient(_create_app(ctr)), ctr  # _create_app 跑 seed_demo：清单有货、两路索引在册


def _spy_swaps(ctr: Container, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """记下经门面发生的每一次别名切换（零部分效果的判据＝这张表为空）。"""
    calls: list[tuple[str, str]] = []
    real = ctr.vectorstore.alias_swap

    def spy(old: str, new: str) -> None:
        calls.append((old, new))
        real(old, new)

    monkeypatch.setattr(ctr.vectorstore, "alias_swap", spy)
    return calls


def _break_swaps(ctr: Container, monkeypatch: pytest.MonkeyPatch) -> None:
    """每一次 alias_swap 都抛（切换与回滚同一条桩）：用于 fail-loud 那条读数——双失败＝别名悬空。"""

    def boom(old: str, new: str) -> None:  # noqa: ARG001
        raise RuntimeError("alias backend down")

    monkeypatch.setattr(ctr.vectorstore, "alias_swap", boom)


def _fail_first_swap(ctr: Container, monkeypatch: pytest.MonkeyPatch) -> None:
    """只有切换那一次抛、回滚那一次放行＝"切换失败但已退回旧别名"这一设计内失败形态。"""
    real = ctr.vectorstore.alias_swap
    left = [1]

    def flaky(old: str, new: str) -> None:
        if left[0]:
            left[0] -= 1
            raise RuntimeError("alias backend down")
        real(old, new)

    monkeypatch.setattr(ctr.vectorstore, "alias_swap", flaky)


def _payload(actor: str = "sre-a", **over: Any) -> dict[str, Any]:
    return {"actor": actor, "confirm": True} | over


def _freeze_b0(ctr: Container) -> None:
    """三次未复位硬断（B1）→ 累积达阈升 B0 冻结全部写侧（禁改区阈值一字未动）。"""
    for _ in range(3):
        with pytest.raises(BreakerTripped):
            ctr.guard.check("batch_delete_ratio", 0.5, "B1", partition="ingest")
    with pytest.raises(BreakerTripped):
        ctr.guard.ensure_write_allowed("ingest")


def test_reindex_flips_alias_and_replay_is_idempotent(tmp_path) -> None:
    """①：POST 重建＝全量重放＋切到新别名；同 chunk_id 幂等复现，块数不增不减、账实不因此新搭背离债。"""
    client, ctr = _wired(tmp_path)
    before = ctr.vectorstore.count()
    assert client.get("/api/ingest/reconcile").json()["missing"] == 0

    rec = client.post("/api/ingest/reindex", json=_payload()).json()
    assert rec["actor"] == "sre-a"
    assert rec["applied"] is True and rec["rolled_back"] is False
    assert rec["active_alias"] == "search_shadow" and rec["error"] is None
    assert ctr.vectorstore.count() == before
    assert client.get("/api/ingest/reconcile").json()["missing"] == 0


def test_three_surfaces_share_one_outcome(tmp_path, capsys) -> None:
    """①：HTTP／CLI／程序化 façade 同函数同读数——门面与命令面不自算第二条口径（`actor` 只在 HTTP 面回显署名）。"""
    client, ctr = _wired(tmp_path)
    http_body: dict[str, Any] = client.post("/api/ingest/reindex", json=_payload()).json()
    assert main(["admin", "reindex", "--confirm"], container=ctr) == 0
    cli_body = json.loads(capsys.readouterr().out)
    direct = ctr.ingest.shadow_rebuild()

    assert set(http_body) == set(_KEYS) | {"actor"}
    assert set(cli_body) == set(_KEYS)
    for key in _KEYS:
        assert http_body[key] == cli_body[key] == getattr(direct, key), f"{key} 三面分叉"


def test_confirm_is_a_gate_not_decoration(tmp_path, monkeypatch) -> None:
    """②：缺 confirm／confirm=false／CLI 未敲 --confirm 一律拒，且拒的时刻一次别名也没切。"""
    client, ctr = _wired(tmp_path)
    calls = _spy_swaps(ctr, monkeypatch)

    assert client.post("/api/ingest/reindex", json={"actor": "sre-a"}).status_code == 422
    assert client.post("/api/ingest/reindex", json=_payload(confirm=False)).status_code == 422
    assert (
        client.post("/api/ingest/alias/revert", json={"actor": "sre-a", "from_alias": "a", "to_alias": "b"})
        .status_code
        == 422
    )  # 缺 confirm＝没确认，即便参数齐全也不切
    with pytest.raises(SystemExit):
        main(["admin", "reindex"], container=ctr)
    with pytest.raises(SystemExit):
        main(["admin", "alias-revert", "--from-alias", "a", "--to-alias", "b"], container=ctr)
    assert calls == []


def test_b0_freeze_stops_reindex_before_any_replay(tmp_path, monkeypatch, capsys) -> None:
    """③：B0 冻结期整批以 503 结束且零部分效果（判门在全量重放之前）；CLI 同一道门、退出码 1。"""
    client, ctr = _wired(tmp_path, hard=True)
    calls = _spy_swaps(ctr, monkeypatch)
    assert ctr.ingest._guard is ctr.guard  # noqa: SLF001 — 接线锁：另造一台闸＝冻结期照样能重建，门就成了摆设
    _freeze_b0(ctr)
    before = ctr.vectorstore.count()

    blocked = client.post("/api/ingest/reindex", json=_payload())
    assert blocked.status_code == 503
    assert "写侧已拉闸（B0）" in json.dumps(blocked.json(), ensure_ascii=False)
    assert calls == [] and ctr.vectorstore.count() == before

    assert main(
        ["admin", "alias-revert", "--from-alias", "search_shadow", "--to-alias", "search_active", "--confirm"],
        container=ctr,
    ) == 1
    assert "BreakerTripped" in capsys.readouterr().err
    assert calls == []  # 切别名同样经本刀补齐的这道门：回滚通道不是拉闸期的后门


def test_switch_failure_reads_as_rollback_not_breakage(tmp_path, monkeypatch) -> None:
    """④：切换抛错→已回滚旧别名＝设计内失败形态，给 200 如实读数，不升格成 5xx、也不谎称 applied。"""
    client, ctr = _wired(tmp_path)
    _fail_first_swap(ctr, monkeypatch)

    rec = client.post("/api/ingest/reindex", json=_payload()).json()
    assert rec["applied"] is False and rec["rolled_back"] is True
    assert rec["active_alias"] == "search_active" and "alias backend down" in str(rec["error"])


def test_double_failure_is_loud_not_silent(tmp_path, monkeypatch, capsys) -> None:
    """⑤：切换与回滚双失败＝别名可能悬空，按 NFR-8 fail-loud 抛 ProviderError；命令面退出码 1，不返回"切完了"。"""
    _client, ctr = _wired(tmp_path)
    _break_swaps(ctr, monkeypatch)

    with pytest.raises(ProviderError):
        ctr.ingest.shadow_rebuild()
    assert main(["admin", "reindex", "--confirm"], container=ctr) == 1
    assert "ProviderError" in capsys.readouterr().err


def test_revert_same_alias_is_rejected_before_touching_store(tmp_path, monkeypatch, capsys) -> None:
    """⑥：同名在请求边界 422（表达不了"切回"），不触库也不经管线；CLI 同名＝退出码 1。"""
    client, ctr = _wired(tmp_path)
    calls = _spy_swaps(ctr, monkeypatch)
    body = _payload(from_alias="search_shadow", to_alias="search_shadow")

    assert client.post("/api/ingest/alias/revert", json=body).status_code == 422
    assert main(
        ["admin", "alias-revert", "--from-alias", "x", "--to-alias", "x", "--confirm"], container=ctr
    ) == 1
    assert calls == []
    assert "ValueError" in capsys.readouterr().err


def test_revert_receipt_names_what_it_does_not_verify(tmp_path) -> None:
    """⑥：执行档单 store 不核验 72h 保留窗——回执以 hint 如实写出这条边界，并指回只读对账（只读不传感）。"""
    client, _ctr = _wired(tmp_path)
    rec = client.post(
        "/api/ingest/alias/revert", json=_payload(from_alias="search_shadow", to_alias="search_active")
    ).json()
    assert rec["swapped"] is True and set(rec) == {"actor", "from_alias", "to_alias", "swapped", "hint"}
    assert "不核验 72h 保留窗" in rec["hint"] and "reconcile" in rec["hint"]


def test_entries_are_deliberately_not_frontend_affordances() -> None:
    """在册不做的半边（刻意）：全量重放与整域切回都是写侧动作，确认门留在命令面/接口面，页面不给"一键重建"。"""
    web = Path(__file__).resolve().parents[2] / "src" / "platform" / "gateway" / "web" / "src"
    pages = sorted(web.rglob("*.tsx")) + sorted(web.rglob("*.ts"))
    assert pages
    texts = [p.read_text(encoding="utf-8").lower() for p in pages]
    # 判据取**端点路径**而非裸词：`reindexed` 是目录入库回执里的既有字段名，子串会误伤。
    assert not [t for t in texts if "/api/ingest/reindex" in t or "/api/ingest/alias/revert" in t]
