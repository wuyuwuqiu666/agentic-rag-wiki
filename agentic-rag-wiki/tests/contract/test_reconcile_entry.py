"""M4-ci B5 对账的**可达入口**契约：HTTP `GET /api/ingest/reconcile` + CLI `admin reconcile`（承 M4-cd 双册账）。

锁三条不变式：
①**同一本账、三个面同一个数**——门面与命令行都不自己算口径，1:1 委派 `IngestPipeline.reconcile(sense=False)`
  （口径分叉＝运维看到的背离债和真实账不一致，比没入口更危险）；
②**读回不传感**——读回会被反复刷新，而 B5 硬断档下每次越阈都记一条未复位触发、连续达阈升 B0 冻结写侧；
  "看一眼账"不得变成"自己把系统拉闸"（故读回侧必须零 breaker_trip，写侧 sense=True 必须仍记）；
③**读回是纯读**——两次读回之间账实计数不得变化，且回执不得声称"已补偿/已修复"（补偿作业仍是写侧动作、无入口）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from src.platform.config import Settings
from src.platform.gateway.cli import main
from src.platform.gateway.composition import Container, build_container
from src.platform.gateway.http import _create_app

_FIELDS = ("expected", "actual", "missing", "extra", "writeback")


def _container(tmp_path, **kwargs: Any) -> Container:
    return build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), **kwargs))


def _b5_trips(ctr: Container) -> list[Any]:
    return [e for e in ctr.audit.read_all() if e.kind == "breaker_trip" and e.extra.get("breaker") == "B5"]


def test_reconcile_is_one_ledger_three_surfaces_no_divergent_math(tmp_path, capsys) -> None:
    """HTTP / CLI / 程序化 façade 三个面报的是同一本账的同一组数（含 M4-cd 两册账的 writeback 与 extra==0）。"""
    ctr = _container(tmp_path)
    client = TestClient(_create_app(ctr))  # _create_app 恒跑 seed_demo：已发布一页 → 回写块在册
    ledger = ctr.ingest.reconcile(sense=False)
    http_body: dict[str, Any] = client.get("/api/ingest/reconcile").json()
    assert main(["admin", "reconcile"], container=ctr) == 0
    cli_body = json.loads(capsys.readouterr().out)

    for name, body in (("http", http_body), ("cli", cli_body)):
        assert {k: body[k] for k in _FIELDS} == ledger, f"{name} 面与管线账不一致（口径分叉）"
    assert http_body["writeback"] >= 1 and http_body["extra"] == 0  # M4-cd：回写块入账，不再虚报孤儿


def test_readback_never_senses_but_the_write_side_still_does(tmp_path) -> None:
    """背离态下的两半锁：读回刷新零 B5 观察；写侧动作收尾的 reconcile() 仍记这一次传感。"""
    ctr = _container(tmp_path, shadow_breakers="")  # 显式转硬断：这一例验的就是"刷新不会累积触发"
    client = TestClient(_create_app(ctr))
    assert _b5_trips(ctr) == []

    indexed = ctr.vectorstore.all_ids()
    assert indexed
    ctr.vectorstore.delete([indexed[0]])  # 制造清单↔索引背离（删一个已索引块）

    for _ in range(5):  # 运维页面反复刷新
        r = client.get("/api/ingest/reconcile")
        assert r.status_code == 200
        assert r.json()["missing"] >= 1
    assert _b5_trips(ctr) == []  # 读回不得记观察——否则 5 次刷新即 5 条未复位触发，达阈升 B0 自伤

    trip = ctr.ingest.reconcile()  # 同一道背离，写侧传感口径必须真记这一次（不得把传感一并删掉）
    assert trip["missing"] >= 1
    events = _b5_trips(ctr)
    assert len(events) == 1 and events[0].extra["shadow"] is False  # 非观察档：这一条是计入台账的真触发


def test_readback_is_pure_and_promises_nothing_it_did_not_do(tmp_path) -> None:
    """两次读回之间账实不变（纯读），回执只指路处置、不声称已补偿或已删孤儿。"""
    ctr = _container(tmp_path)
    client = TestClient(_create_app(ctr))
    first = client.get("/api/ingest/reconcile").json()
    second = client.get("/api/ingest/reconcile").json()
    assert {k: first[k] for k in _FIELDS} == {k: second[k] for k in _FIELDS}
    assert set(first) == set(_FIELDS) | {"hint"}
    hint = first["hint"]
    assert "补偿" in hint and "重编译" in hint  # 分册处置：源块→补偿作业，回写块→B 区重编译重发布
    for forbidden in ("已补偿", "已修复", "已清理"):
        assert forbidden not in hint
