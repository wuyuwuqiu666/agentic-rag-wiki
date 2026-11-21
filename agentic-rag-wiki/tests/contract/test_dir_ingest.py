"""M4-ce 1b 目录批量入库契约：FR-ING-1 存量遍历的本地目录档 + 路径围栏 + 幂等/变更语义。

断言口径（四条，逐条对应本刀的诚实账）：
①`FileSystemSource` 结构满足 `SourcePort`，整目录经 `IngestPipeline.pull` 入库后即被同域问答命中（非幻影）；
②重跑同目录＝幂等跳过（`unchanged` 陈列、索引零增长），"失败/重复点一次"都不产生重复入库（FR-ING-1）；
③文件消失→`delete`（两路索引+清单账同清，语义承 M4-cc/1a）；文件内容变→**只报 `changed` 不入库**
  （执行档 upsert 会重放 objectstore 旧正文，拿它表达"改了"＝静默错值），锁死"本入口不冒充版本递增"这条边界；
④路径围栏三道门（未配根目录／越界／非目录）HTTP 与 CLI 共用 `gateway/dir_ingest.py` 单点口径，
  破坏性批量须 `confirm`；解析失败/超限/撞 id 逐条陈列、不静默丢弃也不阻断整批。

真实源系统读取端（自动抓取、逐文档元数据、断点续传调度）＝放行项㉒；指纹位点＝进程内账，跨进程持久不在本刀承诺内。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from src.partitions.ingest.repo.source import FileSystemSource, SourcePort, doc_id_for
from src.platform.config import Settings
from src.platform.gateway import cli
from src.platform.gateway.composition import Container, build_container
from src.platform.gateway.dir_ingest import (
    DirIngestNotConfigured,
    DirOutsideRoot,
    IngestRootOverlapsArtifact,
    NotAnIngestDirectory,
    run_dir_ingest,
)
from src.platform.gateway.http import _create_app
from src.platform.providers.errors import BreakerTripped
from src.platform.providers.fileparser_dev import DevFileParser
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.types import ChangeEvent


def _body(tag: str) -> str:
    return f"# {tag}规定\n\n" + "\n\n".join(
        f"{tag} 第 {i} 条：北极星{k}专项条款内容为该文档独有，供目录批量入库链检索校验。" for i, k in enumerate(range(6))
    )


def _write(root: Path, name: str, tag: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_body(tag), encoding="utf-8")
    return path


def _container(tmp_path: Path, *, source_dir: str | None = None, hard: bool = False) -> Container:
    """默认按里程碑观察档（`shadow_breakers="B1,B5"`＝只记不拦）：B1「批量删除占比」把"目录里少了一个文件"
    算成 100% 删除占比，硬断档下整批会被判超限——该口径误伤属熔断阈值语义（禁改区，AGENTS 清单 ㉑），
    这里不偷放宽阈值让绿灯好绿，改为两档各自断言（见 `test_hard_breaker_refuses_whole_batch`）。
    """
    return build_container(
        Settings(
            artifact_dir=str(tmp_path / "art"),
            audit_dir=str(tmp_path / "audit"),
            shadow_breakers="" if hard else "B1,B5",
            ingest_source_dir=source_dir,
        )
    )


def _source(root: Path, **kw: object) -> FileSystemSource:
    return FileSystemSource(root, parse=DevFileParser().parse, **kw)  # type: ignore[arg-type]


def _indexed_texts(ctr: Container) -> list[str]:
    """内存档正文读回（同 `/api/source` 口径，须带调用侧可见域）：用于核对"旧块真的不在了、新块真的进了"。"""
    vs = ctr.vectorstore
    assert isinstance(vs, MemoryVectorStore)
    return [c.text for cid in vs.all_ids() if (c := vs.get_chunk(cid, ["fin"])) is not None and c.text]


def test_filesystem_source_satisfies_sourceport(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "甲")
    assert isinstance(_source(tmp_path), SourcePort)  # repo 层契约缝在位，连接器实现不需触碰 service/runtime


def test_doc_id_normalisation_stays_in_id_charset() -> None:
    """doc_id 必须落在 `^[A-Za-z0-9._-]+$`，否则检索期 `{doc}/{ver}#{seq}` 引用 id 解析会错位。"""
    for rel in ("中文目录/员工手册 v2.docx", "a/b~c.md", "....txt", "  "):
        did = doc_id_for(rel)
        assert re.fullmatch(r"[A-Za-z0-9._-]+", did), (rel, did)


def test_dir_batch_ingests_backlog_and_is_searchable(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    _write(root, "fin/allowance.md", "津贴")
    _write(root, "fin/overtime.md", "加班")
    ctr = _container(tmp_path, source_dir=str(root))
    receipt = run_dir_ingest(ctr, domain="fin", doc_type="policy", acl_tags=["fin"])
    assert receipt["ingested"] == 2
    assert receipt["not_ingested"] == 0
    ans = ctr.app.answer("北极星1专项条款怎么规定", user_acl=["fin"])
    assert not ans.refused and ans.citations


def test_rerun_is_idempotent_no_duplicate_indexing(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    _write(root, "a.md", "甲")
    _write(root, "b.md", "乙")
    ctr = _container(tmp_path, source_dir=str(root))
    first = run_dir_ingest(ctr, acl_tags=["fin"])
    ids_after_first = set(ctr.vectorstore.all_ids())  # type: ignore[attr-defined] — 内存档演示读回
    second = run_dir_ingest(ctr, acl_tags=["fin"])
    assert second["ingested"] == 0
    assert len(second["unchanged"]) == 2
    assert set(ctr.vectorstore.all_ids()) == ids_after_first  # 重跑零增长＝无重复入库（FR-ING-1）
    assert first["ingested"] == 2


def test_disappeared_file_becomes_delete(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    keep = _write(root, "keep.md", "留存")
    drop = _write(root, "drop.md", "下架")
    ctr = _container(tmp_path, source_dir=str(root))
    run_dir_ingest(ctr, acl_tags=["fin"])
    drop_ids = set(ctr.ingest._inventory.chunk_ids_for(doc_id_for(str(drop.relative_to(root)))))  # noqa: SLF001 — 测试读内部账
    assert drop_ids <= set(ctr.vectorstore.all_ids())  # type: ignore[attr-defined]
    drop.unlink()
    receipt = run_dir_ingest(ctr, acl_tags=["fin"])
    assert receipt["change_deleted"] >= 1
    assert drop_ids.isdisjoint(set(ctr.vectorstore.all_ids()))  # 两路索引同清（M4-cc）
    assert not ctr.ingest._inventory.chunk_ids_for(doc_id_for(str(drop.relative_to(root))))  # noqa: SLF001
    assert keep.exists()


def test_changed_file_is_reported_not_reingested(tmp_path: Path) -> None:
    """内容变过＝列进 `changed` 且**不入库**：本入口不冒充"同 doc_id 版本递增"（属 M1 冻结 canonical 语义）。"""
    root = tmp_path / "corpus"
    doc = _write(root, "a.md", "甲")
    ctr = _container(tmp_path, source_dir=str(root))
    run_dir_ingest(ctr, acl_tags=["fin"])
    before = set(ctr.vectorstore.all_ids())  # type: ignore[attr-defined]
    doc.write_text(_body("甲") + "\n\n# 修订追加\n\n" + _body("乙修订"), encoding="utf-8")
    receipt = run_dir_ingest(ctr, acl_tags=["fin"])
    assert receipt["ingested"] == 0
    assert [c["doc_id"] for c in receipt["changed"]] == ["a"]
    assert "hint" in receipt and "delete" in receipt["hint"]
    assert set(ctr.vectorstore.all_ids()) == before  # 旧块未被静默替换，也未被重复追加


def test_unparsable_and_empty_files_are_listed_not_dropped(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    _write(root, "ok.md", "正常")
    (root / "bad.bin").write_bytes(b"\x00\x01\x02")
    (root / "empty.md").write_text("   ", encoding="utf-8")
    ctr = _container(tmp_path, source_dir=str(root))
    receipt = run_dir_ingest(ctr, acl_tags=["fin"])
    assert receipt["ingested"] == 1
    assert {s["file"] for s in receipt["skipped"]} == {"bad.bin", "empty.md"}
    assert receipt["not_ingested"] == 2


def test_over_size_file_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    _write(root, "big.md", "超大")
    src = _source(root, max_bytes=64)
    assert list(src.list_documents()) == []
    assert src.skipped and "上限" in src.skipped[0]["reason"]


def test_subdir_target_keeps_stable_identity_and_skips_delete_scan(tmp_path: Path) -> None:
    """两条子目录护栏：①doc_id 相对允许根算→换目标不二次入库；②扫子目录不做消失判定（不静默删兄弟目录）。"""
    root = tmp_path / "corpus"
    _write(root, "hr/a.md", "甲")
    _write(root, "eng/b.md", "乙")
    ctr = _container(tmp_path, source_dir=str(root))
    whole = run_dir_ingest(ctr, acl_tags=["fin"])
    assert whole["delete_scan"] == "root" and whole["ingested"] == 2
    ids = set(ctr.vectorstore.all_ids())  # type: ignore[attr-defined]

    sub = run_dir_ingest(ctr, directory="hr", acl_tags=["fin"])
    assert sub["delete_scan"] == "subdir-off"
    assert sub["ingested"] == 0 and [u["doc_id"] for u in sub["unchanged"]] == ["hr-a"]  # 身份稳定→不重复入库
    assert set(ctr.vectorstore.all_ids()) == ids  # type: ignore[attr-defined]

    (root / "eng" / "b.md").unlink()  # 兄弟目录里的文件消失
    again = run_dir_ingest(ctr, directory="hr", acl_tags=["fin"])
    assert again["change_deleted"] == 0 and set(ctr.vectorstore.all_ids()) == ids  # 子目录扫描不得误删兄弟
    full = run_dir_ingest(ctr, acl_tags=["fin"])
    assert full["change_deleted"] >= 1  # 整根扫描才如实判定


def test_doc_id_collision_within_batch_is_conflict(tmp_path: Path) -> None:
    """两个文件归一出同一 doc_id（`a b.md` 与 `a-b.md`）：后到者进 `conflicts`，不覆盖先入库的那份。"""
    root = tmp_path / "corpus"
    _write(root, "a b.md", "空格名")
    _write(root, "a-b.md", "连字符名")
    ctr = _container(tmp_path, source_dir=str(root))
    receipt = run_dir_ingest(ctr, acl_tags=["fin"])
    assert receipt["ingested"] == 1
    assert [c["file"] for c in receipt["conflicts"]] == ["a-b.md"]


def test_hidden_files_are_skipped_by_relative_parts(tmp_path: Path) -> None:
    root = tmp_path / ".corpus"  # 根目录自身带点也须看得见内部文件（绝对前缀不参与隐藏判定）
    _write(root, "visible.md", "可见")
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "config.md").write_text(_body("忽略"), encoding="utf-8")
    src = _source(root)
    assert [d.doc_id for d in src.list_documents()] == ["visible"]


# ── 围栏三道门（模块单点）+ HTTP/CLI 共用口径 ──


def test_fence_requires_configured_root(tmp_path: Path) -> None:
    ctr = _container(tmp_path)  # 未配 ingest_source_dir
    with pytest.raises(DirIngestNotConfigured):
        run_dir_ingest(ctr)
    rc = cli.main(["ingest", "--confirm"], container=ctr)
    assert rc == 1  # CLI 同口径 fail-loud，不静默空批


def test_fence_rejects_escape_and_non_directory(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    (tmp_path / "outside.md").write_text(_body("界外"), encoding="utf-8")
    ctr = _container(tmp_path, source_dir=str(root))
    with pytest.raises(DirOutsideRoot):
        run_dir_ingest(ctr, directory="../")
    with pytest.raises(DirOutsideRoot):  # 绝对路径直指根外也不放行
        run_dir_ingest(ctr, directory=str(tmp_path))
    with pytest.raises(NotAnIngestDirectory):
        run_dir_ingest(ctr, directory="nope")


def test_fence_refuses_self_feeding_target(tmp_path: Path) -> None:
    """自灌围栏：摄取根覆盖系统产物目录（canonical 草稿/决议记录/对账报告就写在那）＝整批拒绝，绝不吸自己的中间件。"""
    root = tmp_path / "corpus"
    root.mkdir()
    ctr = build_container(
        Settings(
            artifact_dir=str(root / "art"),  # 产物落在摄取根之内＝典型误配
            audit_dir=str(tmp_path / "audit"),
            ingest_source_dir=str(root),
        )
    )
    with pytest.raises(IngestRootOverlapsArtifact):
        run_dir_ingest(ctr)


def _client(tmp_path: Path, *, source_dir: str | None = None, hard: bool = False) -> TestClient:
    return TestClient(_create_app(_container(tmp_path, source_dir=source_dir, hard=hard)))


def test_http_dir_endpoint_status_codes(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    _write(root, "a.md", "津贴")
    client = _client(tmp_path)
    assert client.post("/api/ingest/dir", json={"directory": "", "actor": "op", "confirm": True}).status_code == 501
    configured = _client(tmp_path, source_dir=str(root))
    assert configured.post("/api/ingest/dir", json={"directory": "../", "actor": "op", "confirm": True}).status_code == 403
    assert configured.post("/api/ingest/dir", json={"directory": "nope", "actor": "op", "confirm": True}).status_code == 422
    assert configured.post("/api/ingest/dir", json={"directory": "", "actor": "op"}).status_code == 422  # confirm 必填
    assert configured.post("/api/ingest/dir", json={"directory": "", "actor": ""}).status_code == 422  # 署名必填（T2）


def test_run_dir_ingest_reingests_after_manual_retire(tmp_path: Path) -> None:
    """`changed` 的指路确实走得通：先按回执下架该 doc_id，再重跑目录→新正文入库、旧块不在。"""
    root = tmp_path / "corpus"
    doc = _write(root, "a.md", "甲")
    ctr = _container(tmp_path, source_dir=str(root))
    run_dir_ingest(ctr, acl_tags=["fin"])
    doc.write_text(_body("甲改"), encoding="utf-8")
    receipt = run_dir_ingest(ctr, acl_tags=["fin"])
    assert [c["doc_id"] for c in receipt["changed"]] == ["a"]
    ctr.ingest.apply_changes([ChangeEvent(doc_id="a", kind="delete", version=1)])
    again = run_dir_ingest(ctr, acl_tags=["fin"])
    assert again["ingested"] == 1 and again["changed"] == []
    assert [r["doc_id"] for r in again["reingested"]] == ["a"]  # 在册判据转假→按源为真相重新入库
    texts = " ".join(_indexed_texts(ctr))
    assert "甲改" in texts and "甲 第 0 条" not in texts


def test_retired_doc_is_reingested_because_source_is_truth(tmp_path: Path) -> None:
    """人工下架后**文件未变**也要能重跑回来：位点账只加速，`is_indexed` 才裁决（与 FR-ING-4 补偿同口径）。"""
    root = tmp_path / "corpus"
    doc = _write(root, "a.md", "甲")
    ctr = _container(tmp_path, source_dir=str(root))
    run_dir_ingest(ctr, acl_tags=["fin"])
    ctr.ingest.apply_changes([ChangeEvent(doc_id="a", kind="delete", version=1)])
    assert not ctr.ingest.is_indexed("a")
    receipt = run_dir_ingest(ctr, acl_tags=["fin"])
    assert receipt["ingested"] == 1 and [r["doc_id"] for r in receipt["reingested"]] == ["a"]
    assert ctr.ingest.is_indexed("a") and doc.exists()


def test_hard_breaker_refuses_whole_batch_with_zero_effect(tmp_path: Path) -> None:
    """硬断档（M4 清空 shadow）下 B1 拦整批：`run_dir_ingest` 抛 BreakerTripped、已入库的块一条没少（零部分效果）。

    这是 1a 同一条在册口径（AGENTS 清单 ㉑）在目录批量入口的复现——单文件从目录消失＝删除占比 100%。
    本例只锁"闸真的拦、且不半途"，不声称"单文档下架在硬断档下可用"（那须阈值语义 ADR）。
    """
    root = tmp_path / "corpus"
    keep = _write(root, "keep.md", "留存")
    drop = _write(root, "drop.md", "下架")
    ctr = _container(tmp_path, source_dir=str(root), hard=True)
    run_dir_ingest(ctr, acl_tags=["fin"])
    before = set(ctr.vectorstore.all_ids())  # type: ignore[attr-defined]
    drop.unlink()
    with pytest.raises(BreakerTripped):
        run_dir_ingest(ctr, acl_tags=["fin"])
    assert set(ctr.vectorstore.all_ids()) == before  # 未半途清理：整批被拒
    assert keep.exists()


def test_http_dir_ingest_then_ask(tmp_path: Path) -> None:
    """HTTP 半边：整目录入库的批次账可读回，同域问答即命中；越权主体不因"整目录"而拿到该批正文。"""
    root = tmp_path / "corpus"
    _write(root, "allowance.md", "差旅")
    client = _client(tmp_path, source_dir=str(root))
    r = client.post("/api/ingest/dir", json={"directory": "", "domain": "fin", "acl_tags": ["fin"], "actor": "op", "confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] == 1 and body["actor"] == "op"
    ans = client.post("/api/ask", json={"question": "北极星2专项条款是什么", "acl": ["fin"]}).json()
    assert not ans["refused"] and ans["citations"]
    # 越权主体不得因"整目录入库"而拿到该批正文（ACL 仍由管线与 pre-filter 执法，本入口零旁路）
    out = client.post("/api/ask", json={"question": "北极星2专项条款是什么", "acl": ["other"]}).json()
    assert all("北极星2" not in c.get("text", "") for c in out["citations"]) or out["refused"]
