"""M4-cj 补偿的**写侧可达入口**契约：HTTP `POST /api/ingest/compensate` + CLI `admin compensate`（承 M4-ci 只读那半边）。

锁六条不变式：
①**读/写分门**——`GET /api/ingest/reconcile` 只给数不动账，`POST` 才改索引；把补偿做成 GET 的副作用＝"看一眼账"顺手把库改了；
②**同一本账、三个面同一个数**——HTTP / CLI / 程序化 façade 全 1:1 委派 `IngestPipeline.compensate`，门面不自算口径；
③**confirm 是门不是装饰**——缺省、false、CLI 没敲 `--confirm` 一律拒，且拒绝时刻零写侧效果（索引不动、报告不落）；
④**判门在写侧之前**——B5 硬断档下整批以 503 结束且**零部分效果**（无半截重放、无报告、无回执），一次动作只传感一次；
  读回侧仍零观察（M4-ci 的"只读不传感"不因本刀破功）；
⑤**补不了的不假装修**——回写块缺账（属已发布权威页）不经此补：`backfilled=0`、`missing_after` 如实留账，不臆造那张页。
⑥**B0 总闸也在这一步之前**（M4-cv／审计 #14）——拉闸期点补偿不得给出"成功形状"回执。旧实现只有 B5 那一道 `check`，
  B0 的门住在 `process_document` 里，于是"会不会被拒"取决于**这一批里有没有一条正文真能重放**：`missing=0` 时循环体
  一次都不跑，冻结期照样落报告、返回 200＋`backfilled:0`（运维读成"账实相符、不必处理"）。前置后的判据：503 ∧ 零重放
  ∧ 零报告 ∧ 索引不动 ∧ 一次动作只被问一次，且 CLI 同一道门；读侧不受此门影响。
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
from src.platform.providers.errors import BreakerTripped
from src.platform.types import Document

_BODY = "# 章节\n\n" + "\n\n".join(
    f"第 {i} 段：这里写明一项报销规则的具体数额与适用条件，供检索与补偿重放使用。" for i in range(8)
)
_FIELDS = ("expected", "actual", "extra", "writeback")


def _ctr(tmp_path, *, hard: bool = False) -> Container:
    return build_container(
        Settings(
            artifact_dir=str(tmp_path),
            audit_dir=str(tmp_path / "audit"),
            **({"shadow_breakers": ""} if hard else {}),  # 缺省＝里程碑观察档（B5 只记不拦）
        )
    )


def _wired(tmp_path, *, hard: bool = False) -> tuple[TestClient, Container]:
    ctr = _ctr(tmp_path, hard=hard)
    return TestClient(_create_app(ctr)), ctr  # _create_app 跑 seed_demo：已发布一页→回写块在册


def _b5_trips(ctr: Container) -> list[Any]:
    return [e for e in ctr.audit.read_all() if e.kind == "breaker_trip" and e.extra.get("breaker") == "B5"]


def _b0_readings(ctr: Container) -> int:
    return sum(
        1 for e in ctr.audit.read_all() if e.kind == "breaker_trip" and e.extra.get("breaker") == "B0"
    )


def _bulk(ctr: Container, n: int) -> list[str]:
    """灌 n 条单块文档并返回它们的 chunk_id（制造"清单有、索引没有"的背离债的原料）。"""
    ids: list[str] = []
    for i in range(n):
        doc_id = f"bulk-{i:03d}"
        ctr.ingest.process_document(Document(doc_id=doc_id, version=1, domain="hr", acl_tags=["hr"], raw_text=_BODY))
        ids += [c for c in ctr.vectorstore.all_ids() if c.startswith(f"{doc_id}/")]
    assert len(ids) == n
    return ids


def _drop(ctr: Container, ids: list[str]) -> list[str]:
    """只从索引侧删（清单仍记＝expected）：模拟索引掉块，A 区正文与账都在，故这份缺账是可补的那一份。"""
    live = set(ctr.vectorstore.all_ids())
    gone = [c for c in ids if c in live]
    assert gone
    ctr.vectorstore.delete(gone)
    return gone


def _payload(actor: str = "sre-a", **over: Any) -> dict[str, Any]:
    return {"actor": actor, "confirm": True} | over


def _freeze_b0(ctr: Container) -> None:
    """三次未复位硬断（B1）→ 累积达阈升 B0 冻结全部写侧（禁改区阈值一字未动）。"""
    for _ in range(3):
        with pytest.raises(BreakerTripped):
            ctr.guard.check("batch_delete_ratio", 0.5, "B1", partition="ingest")
    with pytest.raises(BreakerTripped):
        ctr.guard.ensure_write_allowed("ingest")


def _spy_replays(ctr: Container, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """记下经补偿发生的每一次重放（零部分效果的判据＝这张表为空）； spy 挂在恢复/补偿/改域共用的唯一重放口上。"""
    calls: list[Any] = []
    real = ctr.ingest._reindex_event  # noqa: SLF001 — 测的就是这个私有缝：绕过它另走一条重放路＝补偿面藏了后门

    def spy(ev: Any) -> bool:
        calls.append(ev)
        return real(ev)

    monkeypatch.setattr(ctr.ingest, "_reindex_event", spy)
    return calls


def test_post_compensate_repairs_source_blocks_and_the_readback_agrees(tmp_path) -> None:
    """①+②：POST 补源块、GET 只报数；补完后 GET 的账与回执同一个口径，回执里的报告路径是真产物。"""
    client, ctr = _wired(tmp_path)
    gone = _drop(ctr, _bulk(ctr, 3))

    assert client.get("/api/ingest/reconcile").json()["missing"] == len(gone)

    rec = client.post("/api/ingest/compensate", json=_payload()).json()
    assert rec["actor"] == "sre-a"
    assert rec["missing"] == len(gone) and rec["missing_source"] == len(gone)
    assert rec["backfilled"] == len(gone) and rec["missing_after"] == 0  # 补得回的补完，剩债如实归零
    assert set(gone) <= set(ctr.vectorstore.all_ids())  # 索引真长回来了（同 chunk_id 幂等重放）

    after = client.get("/api/ingest/reconcile").json()
    assert after["missing"] == 0 and after["expected"] == rec["expected"]  # 读回与回执不分叉
    report = Path(str(rec["report"]))
    assert report.is_file() and gone[0] in report.read_text(encoding="utf-8")


def test_three_surfaces_share_one_ledger(tmp_path, capsys) -> None:
    """②：三个面给同一本账（补偿口径只有管线这一份；门面与命令面零新逻辑，`actor` 只在 HTTP 面回显署名）。"""
    client, ctr = _wired(tmp_path)
    http_body: dict[str, Any] = client.post("/api/ingest/compensate", json=_payload()).json()
    assert main(["admin", "compensate", "--confirm"], container=ctr) == 0
    cli_body = json.loads(capsys.readouterr().out)
    ledger = ctr.ingest.compensate(write_report=False)

    for name, body in (("http", http_body), ("cli", cli_body), ("façade", ledger)):
        assert {k: body[k] for k in _FIELDS} == {k: ledger[k] for k in _FIELDS}, f"{name} 面口径分叉"
    assert set(cli_body) == set(ledger)
    assert set(http_body) == set(ledger) | {"actor"}


def test_confirm_is_a_gate_not_decoration(tmp_path) -> None:
    """③：缺 confirm／confirm=false／CLI 未敲 --confirm 一律拒，且拒了就是什么都没补（索引没动、报告没落）。"""
    client, ctr = _wired(tmp_path)
    gone = _drop(ctr, _bulk(ctr, 2))

    assert client.post("/api/ingest/compensate", json={"actor": "sre-a"}).status_code == 422
    assert client.post("/api/ingest/compensate", json=_payload(confirm=False)).status_code == 422
    with pytest.raises(SystemExit):
        main(["admin", "compensate"], container=ctr)

    assert ctr.ingest.reconcile(sense=False)["missing"] == len(gone)
    assert not (Path(str(tmp_path)) / "reconcile").exists()


def test_hard_b5_trips_before_any_write(tmp_path, capsys) -> None:
    """④：硬断档下整批 503 且零部分效果——先判门再动手，且一次动作只传感一次；随后看账仍不构成触发（M4-ci）。"""
    client, ctr = _wired(tmp_path, hard=True)
    gone = _drop(ctr, _bulk(ctr, 105))  # ≥ b5_missing 硬阈（100），禁改区阈值不为此例放宽

    blocked = client.post("/api/ingest/compensate", json=_payload())
    assert blocked.status_code == 503
    detail = json.dumps(blocked.json(), ensure_ascii=False)
    assert "B5" in detail and "（B0）" not in detail  # 回执点名真闸：误标 B0 会让人去复位一条根本没触发的总闸
    assert not (set(gone) & set(ctr.vectorstore.all_ids()))  # 没有"补了一半"的中间态
    assert not (Path(str(tmp_path)) / "reconcile").exists()  # 报告也没落＝不产半账
    trips = _b5_trips(ctr)
    assert len(trips) == 1 and trips[0].extra["action"] == "block" and trips[0].extra["shadow"] is False

    assert main(["admin", "compensate", "--confirm"], container=ctr) == 1  # 命令行同一道门，同一次传感
    assert "BreakerTripped" in capsys.readouterr().err
    assert len(_b5_trips(ctr)) == 2

    assert client.get("/api/ingest/reconcile").status_code == 200
    assert len(_b5_trips(ctr)) == 2  # 读回不加观察：拉闸之后"看一眼账"不会把账再拉一次


def test_b0_freeze_stops_compensate_before_the_replay(tmp_path, monkeypatch, capsys) -> None:
    """⑥：B0 冻结期整批在第一步被拒——一次重放没跑、一份报告没落、一格索引没动；CLI 同一道门。"""
    client, ctr = _wired(tmp_path, hard=True)
    assert ctr.ingest._guard is ctr.guard  # noqa: SLF001 — 接线锁：另造一台闸＝冻结期照样能补偿，门就成了摆设
    replays = _spy_replays(ctr, monkeypatch)
    gone = _drop(ctr, _bulk(ctr, 3))
    _freeze_b0(ctr)
    n0, before = _b0_readings(ctr), ctr.vectorstore.count()

    blocked = client.post("/api/ingest/compensate", json=_payload())
    assert blocked.status_code == 503
    detail = json.dumps(blocked.json(), ensure_ascii=False)
    assert "写侧已拉闸（B0）" in detail  # 债只有 3 块（<100）：拦下它的必须是总闸，不是单类闸
    assert replays == [] and ctr.vectorstore.count() == before
    assert set(gone).isdisjoint(ctr.vectorstore.all_ids())  # 没"补了一半"，也没有半路补进去的
    assert not (Path(str(tmp_path)) / "reconcile").exists()
    assert _b0_readings(ctr) == n0 + 1  # 一次动作只被问这一次（门若落在循环里就是逐 doc 若干次）

    assert main(["admin", "compensate", "--confirm"], container=ctr) == 1
    assert "BreakerTripped" in capsys.readouterr().err
    assert replays == [] and _b0_readings(ctr) == n0 + 2


def test_zero_debt_under_freeze_is_refused_not_a_200(tmp_path) -> None:
    """⑥ 的正身（审计 #14）：账实相符时旧实现给 200＋`backfilled:0`＋报告，运维读成"不必处理"——现在拒在门口。"""
    client, ctr = _wired(tmp_path, hard=True)
    assert ctr.ingest.reconcile(sense=False)["missing"] == 0  # 债为零＝重放循环根本碰不到 process_document 里那道门
    _freeze_b0(ctr)

    blocked = client.post("/api/ingest/compensate", json=_payload())
    assert blocked.status_code == 503
    assert "写侧已拉闸（B0）" in json.dumps(blocked.json(), ensure_ascii=False)
    assert not (Path(str(tmp_path)) / "reconcile").exists()  # 冻结期不产报告＝不产一份"看起来处理过了"的半账
    assert len(_b5_trips(ctr)) == 0  # 补偿被 B0 拒时不牵动 B5 的账

    assert client.get("/api/ingest/reconcile").status_code == 200  # 总闸冻写侧、保读侧：看账的本事不能被自己削掉


def test_writeback_debt_is_never_fabricated_by_the_entry(tmp_path) -> None:
    """⑤：只缺权威页回写块时入口给 200 但 backfilled=0、债照旧留账——不臆造那张页，指回 B 区重编译。"""
    client, ctr = _wired(tmp_path)
    wb = list(ctr.inventory.writeback_ids())
    assert wb
    gone = _drop(ctr, wb)

    rec = client.post("/api/ingest/compensate", json=_payload()).json()
    assert rec["missing"] == len(gone) and rec["missing_writeback"] == len(gone)
    assert rec["missing_source"] == 0 and rec["backfilled"] == 0
    assert rec["missing_after"] == len(gone)
    assert "重编译" in client.get("/api/ingest/reconcile").json()["hint"]


def test_entry_is_deliberately_not_a_frontend_affordance() -> None:
    """在册不做的半边（刻意）：补偿是批量写侧动作，执行档故意不给页面按钮——前端只有读回，没有"一键修复"。"""
    web = Path(__file__).resolve().parents[2] / "src" / "platform" / "gateway" / "web" / "src"
    pages = sorted(web.rglob("*.tsx")) + sorted(web.rglob("*.ts"))
    assert pages
    assert not [p for p in pages if "compensate" in p.read_text(encoding="utf-8").lower()]
