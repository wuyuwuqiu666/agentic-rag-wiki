"""新增护栏脚本单测：runbook 完整性（E10）+ 覆盖率门禁（E7）+ 契约向后兼容（E3）——纯逻辑可离线验证，不依赖真实 coverage。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from src.platform.harness import contract_compat, coverage_gate, param_class_check, runbook_check
from src.platform.harness.breaker_rules import validate as breaker_validate
from src.platform.harness.schemas import validate as schemas_validate


def test_real_repo_runbooks_complete() -> None:
    """当前仓库全部分支熔断均有处置页（真实集成守卫，缺口即 CI 红）。"""
    assert runbook_check.check() == []


def test_documented_ids_scans_headings(tmp_path: Path) -> None:
    (tmp_path / "r.md").write_text("# t\n\n## B1 x\n\n## B3 y\ntext ## B7 not a heading line-start ok", encoding="utf-8")
    assert runbook_check._documented_ids(tmp_path) == {"B1", "B3"}


def test_breaker_ids_reads_yaml(tmp_path: Path) -> None:
    (tmp_path / "b1.yaml").write_text(yaml.safe_dump({"breaker": "B1", "metric": "m"}), encoding="utf-8")
    (tmp_path / "b5.yaml").write_text(yaml.safe_dump([{"breaker": "B5", "metric": "a"}, {"breaker": "B5", "metric": "b"}]), encoding="utf-8")
    assert runbook_check._breaker_ids(tmp_path) == {"B1", "B5"}


def test_check_flags_missing_runbook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "b9.yaml").write_text(yaml.safe_dump({"breaker": "B9", "metric": "m"}), encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "b.md").write_text("## B1 only\n", encoding="utf-8")
    monkeypatch.setattr(runbook_check, "_RULES_DIR", rules)
    monkeypatch.setattr(runbook_check, "_RUNBOOKS_DIR", docs)
    problems = runbook_check.check()
    assert any("B9" in p for p in problems)


# ── ②「每可达入口须有处置页」（M4-cl）：入口清单由代码派生，矩阵只许 1:1 跟随 ──────────
_HTTP_SRC = '''
@app.get("/api/health")
def health(): ...

@app.post("/api/ingest/dir")
def ingest_dir(): ...
'''

# 故意混三种注册写法：单行、跨行续写、链式定义子命令组（少认一种＝矩阵少一条也没人报警）
_CLI_SRC = '''
def _build_parser():
    p = argparse.ArgumentParser(prog="x")
    sub = p.add_subparsers(dest="command")
    ask = sub.add_parser("ask", help="问答（含括号）")
    ing = sub.add_parser(
        "ingest",
        help="目录批量入库",
    )
    adm = sub.add_parser("admin", help="运维面").add_subparsers(dest="admin_command")
    adm.add_parser("pool")
    adm.add_parser(
        "reset",
        help="双人复位",
    )
    return p
'''


def test_http_entries_derive_from_decorators() -> None:
    assert runbook_check._http_entries(_HTTP_SRC) == ["GET /api/health", "POST /api/ingest/dir"]


def test_cli_entries_group_multiline_and_chained_registrations() -> None:
    entries, problems = runbook_check._cli_entries(_CLI_SRC)
    assert problems == []
    assert entries == ["cli ask", "cli ingest", "cli admin", "cli admin pool", "cli admin reset"]


def test_cli_entries_fail_loud_when_registration_unattributed() -> None:
    entries, problems = runbook_check._cli_entries('orphan = make_group()\norphan.add_parser("x")\n')
    assert entries == []
    assert any("无法归组" in p for p in problems)


def _patch_entry_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: list[str],
    pages: tuple[str, ...] = ("x.md",),
    cli_src: str = _CLI_SRC,
) -> None:
    """造一个最小假仓：门面源码 + 总矩阵 + 处置页，判据只认这三样是否在册。"""
    gateway = tmp_path / "src" / "platform" / "gateway"
    gateway.mkdir(parents=True)
    (gateway / "http.py").write_text(_HTTP_SRC, encoding="utf-8")
    (gateway / "cli.py").write_text(cli_src, encoding="utf-8")
    docs = tmp_path / "docs" / "runbooks"
    docs.mkdir(parents=True)
    for page in pages:
        (docs / page).write_text("## B1 现象\n", encoding="utf-8")
    body = "\n".join(f"| `{row}` | HTTP | `x.md` | 口径 |" for row in rows)
    (docs / "entry_matrix.md").write_text(
        f"| 入口 | 面 | 处置页 | 一句话口径 |\n|---|---|---|---|\n{body}\n", encoding="utf-8"
    )
    monkeypatch.setattr(runbook_check, "_SRC", tmp_path / "src")
    monkeypatch.setattr(runbook_check, "_RUNBOOKS_DIR", docs)
    monkeypatch.setattr(runbook_check, "_MATRIX", docs / "entry_matrix.md")
    monkeypatch.setattr(runbook_check, "_REPO", tmp_path)


_ALL_ROWS = ["GET /api/health", "POST /api/ingest/dir", "cli ask", "cli ingest", "cli admin",
             "cli admin pool", "cli admin reset"]


def test_entry_check_green_when_matrix_matches_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_repo(tmp_path, monkeypatch, rows=_ALL_ROWS)
    assert runbook_check._entry_problems() == []


def test_entry_check_flags_missing_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_repo(tmp_path, monkeypatch, rows=[r for r in _ALL_ROWS if r != "cli admin reset"])
    problems = runbook_check._entry_problems()
    assert any("可达入口未进总矩阵" in p and "cli admin reset" in p for p in problems)


def test_entry_check_flags_phantom_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_repo(tmp_path, monkeypatch, rows=_ALL_ROWS + ["POST /api/does/not/exist"])
    problems = runbook_check._entry_problems()
    assert any("幻影入口行" in p for p in problems)


def test_entry_check_flags_duplicate_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_repo(tmp_path, monkeypatch, rows=_ALL_ROWS + ["GET /api/health"])
    problems = runbook_check._entry_problems()
    assert any("总矩阵重复行" in p for p in problems)


def test_entry_check_flags_missing_cited_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_repo(tmp_path, monkeypatch, rows=_ALL_ROWS, pages=())
    problems = runbook_check._entry_problems()
    assert any("指路不存在的页" in p and "x.md" in p for p in problems)


def test_entry_check_flags_ungroupable_cli_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_repo(tmp_path, monkeypatch, rows=_ALL_ROWS, cli_src='orphan.add_parser("surprise")\n')
    problems = runbook_check._entry_problems()
    assert any("无法归组" in p for p in problems)


def test_real_repo_matrix_covers_every_entry() -> None:
    """真实仓守卫：总矩阵首列＝代码派生的全部可达入口，一条不许多、一条不许少。"""
    http_text = (runbook_check._SRC / "platform" / "gateway" / "http.py").read_text(encoding="utf-8")
    cli_text = (runbook_check._SRC / "platform" / "gateway" / "cli.py").read_text(encoding="utf-8")
    cli_entries, cli_problems = runbook_check._cli_entries(cli_text)
    assert cli_problems == []
    derived = set(runbook_check._http_entries(http_text)) | set(cli_entries)
    documented = runbook_check._matrix_rows(runbook_check._MATRIX)[0]
    assert set(documented) == derived
    assert len(documented) == len(derived)


def _cov(files: dict[str, dict], total: float) -> dict:
    return {"files": files, "totals": {"percent_covered": total}}


def _entry(statements: int, covered: int) -> dict:
    return {"summary": {"num_statements": statements, "covered_lines": covered}}


def test_coverage_gate_passes() -> None:
    cov = _cov(
        {
            "src/platform/breaker/engine.py": _entry(100, 95),
            "src/partitions/ingest/service/ask/fusion.py": _entry(50, 46),
            "src/platform/gateway/app.py": _entry(100, 80),
        },
        85.0,
    )
    assert coverage_gate.evaluate(cov) == []


def test_coverage_gate_high_package_below_floor() -> None:
    cov = _cov({"src/platform/breaker/engine.py": _entry(100, 70)}, 85.0)  # 70% < 90%
    problems = coverage_gate.evaluate(cov)
    assert any("src/platform/breaker" in p for p in problems)


def test_coverage_gate_overall_below_floor() -> None:
    cov = _cov({"src/platform/breaker/engine.py": _entry(100, 100)}, 60.0)  # 整体 60% < 70%
    assert any("整体覆盖率" in p for p in coverage_gate.evaluate(cov))


def test_coverage_gate_missing_instrumentation() -> None:
    cov = _cov({"src/platform/gateway/app.py": _entry(100, 90)}, 90.0)  # 高门槛包一条都没有
    problems = coverage_gate.evaluate(cov)
    assert any("src/platform/breaker" in p and "未匹配" in p for p in problems)


def test_coverage_gate_backslash_paths_normalized() -> None:
    cov = _cov(
        {
            "src\\platform\\breaker\\engine.py": _entry(100, 92),
            "src\\partitions\\ingest\\service\\ask\\fusion.py": _entry(50, 48),
        },
        92.0,
    )
    assert coverage_gate.evaluate(cov) == []


# —— contract_compat（E3）：只拦破坏兼容，新增放行；真实基线与当前契约面对齐 ——
def test_real_repo_contract_backward_compatible() -> None:
    """已提交基线 vs 当前 types 契约面：无删字段/改类型（真实守卫，契约被静默收窄即 CI 红）。"""
    assert contract_compat.check() == []


def test_diff_flags_removed_field() -> None:
    base = {"Chunk": {"doc_id": "str", "seq": "int"}}
    cur = {"Chunk": {"doc_id": "str"}}  # 丢了 seq
    assert any("Chunk.seq" in p and "删除" in p for p in contract_compat.diff(base, cur))


def test_diff_flags_type_change() -> None:
    base = {"Chunk": {"seq": "int"}}
    cur = {"Chunk": {"seq": "str"}}
    assert any("Chunk.seq" in p and "类型变更" in p for p in contract_compat.diff(base, cur))


def test_diff_flags_removed_model() -> None:
    base = {"Answer": {"question": "str"}, "WikiPage": {"page_id": "str"}}
    cur = {"Answer": {"question": "str"}}  # WikiPage 整块没了
    assert any("WikiPage" in p and "移除" in p for p in contract_compat.diff(base, cur))


def test_diff_allows_additive_backwards_compatible() -> None:
    base = {"Chunk": {"doc_id": "str"}}
    cur = {"Chunk": {"doc_id": "str", "new_field": "int"}, "BrandNew": {"x": "str"}}  # 加字段+加模型
    assert contract_compat.diff(base, cur) == []


def test_missing_baseline_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contract_compat, "_BASELINE", tmp_path / "nope.json")
    problems = contract_compat.check()
    assert problems and "基线缺失" in problems[0]


# —— breaker adr 追溯：不止查字段存在，还须解析到 docs/adr 真实文件（M4-s 收口"查存在不查正确"盲点）——
def _rule_yaml(adr: str | None) -> dict:
    d = {"breaker": "B1", "metric": "batch_delete_ratio", "comparator": ">=", "level": "soft", "soft": 1, "hard": 2}
    if adr is not None:
        d["adr"] = adr
    return d


def test_real_repo_breaker_adr_references_resolve() -> None:
    """真实仓库：每条 breaker 规则的 adr 都能在 docs/adr 找到对应文件（错挂/悬空即 CI 红）。"""
    assert breaker_validate.check() == []


def test_existing_adr_ids_scans_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ADR-003-b0-total-gate.md").write_text("# x", encoding="utf-8")
    (tmp_path / "ADR-008-chunking.md").write_text("# y", encoding="utf-8")
    (tmp_path / "README.md").write_text("# idx", encoding="utf-8")
    monkeypatch.setattr(breaker_validate, "_ADR_DIR", tmp_path)
    assert breaker_validate._existing_adr_ids() == {"ADR-003", "ADR-008"}


def test_adr_unresolved_is_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "b1.yaml").write_text(yaml.safe_dump(_rule_yaml("ADR-099")), encoding="utf-8")
    adrs = tmp_path / "adr"
    adrs.mkdir()
    (adrs / "ADR-002-dual-write.md").write_text("# x", encoding="utf-8")
    monkeypatch.setattr(breaker_validate, "_RULES_DIR", rules)
    monkeypatch.setattr(breaker_validate, "_ADR_DIR", adrs)
    problems = breaker_validate.check()
    assert any("ADR-099" in p and "无对应文件" in p for p in problems)  # 挂错号被抓（正是 b0/b4 的历史缺陷）


def test_adr_present_but_target_exists_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "b1.yaml").write_text(yaml.safe_dump(_rule_yaml("ADR-002")), encoding="utf-8")
    adrs = tmp_path / "adr"
    adrs.mkdir()
    (adrs / "ADR-002-dual-write-order.md").write_text("# x", encoding="utf-8")
    monkeypatch.setattr(breaker_validate, "_RULES_DIR", rules)
    monkeypatch.setattr(breaker_validate, "_ADR_DIR", adrs)
    assert breaker_validate.check() == []


def test_missing_adr_field_still_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "b1.yaml").write_text(yaml.safe_dump(_rule_yaml(None)), encoding="utf-8")
    adrs = tmp_path / "adr"
    adrs.mkdir()
    monkeypatch.setattr(breaker_validate, "_RULES_DIR", rules)
    monkeypatch.setattr(breaker_validate, "_ADR_DIR", adrs)
    problems = breaker_validate.check()
    assert any("缺 adr 引用" in p for p in problems)


# —— schemas.validate（产物 #3）：契约字段漂移/缺失须被 check() 抓到（查得到≠查得准，M4-w）——
def test_real_repo_schemas_contract_complete() -> None:
    """真实仓库全部一级契约必填字段在册（漂移即 CI 红）。"""
    assert schemas_validate.check() == []


def test_schemas_required_fields_actually_present() -> None:
    """抽查：REQUIRED_FIELDS 声明的字段须真在对应模型里——防清单本身与模型脱节致 check 空转。"""
    from src.platform.types import Answer, Chunk

    assert {"chunk_id", "doc_id", "acl_tags", "embedding_model"} <= set(Chunk.model_fields)
    assert {"question", "citations"} <= set(Answer.model_fields)


def test_schemas_detects_field_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """清单声称某字段为必填但模型无=契约漂移，check() 须报。"""
    monkeypatch.setattr(schemas_validate, "REQUIRED_FIELDS", {"Chunk": {"nonexistent_field_xyz"}})
    problems = schemas_validate.check()
    assert any("Chunk" in p and "漂移" in p and "nonexistent_field_xyz" in p for p in problems)


def test_schemas_detects_missing_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """契约名声称存在但 _MODELS 无（未从 types 导出）=契约缺失。"""
    monkeypatch.setattr(schemas_validate, "REQUIRED_FIELDS", {"GhostModel": {"x"}})
    problems = schemas_validate.check()
    assert any("GhostModel" in p and "未在 types 导出" in p for p in problems)


def test_decision_contracts_are_in_e3_snapshot_scope() -> None:
    """决议契约三级必须留在 E3 快照集合里（M4-ck）：被移出 _MODELS 或基线即重新变成"一级契约无门"。"""
    names = {"CanonicalNode", "NodeDecision", "ChunkDecision"}
    assert names <= set(schemas_validate._MODELS)
    baseline = json.loads(contract_compat._BASELINE.read_text(encoding="utf-8"))
    assert names <= set(baseline)


# —— param_class_check（产物 #4a）：双级白名单一致性 + 安全参数禁自动生效须被 check() 抓到 ——
def test_real_repo_param_class_consistent() -> None:
    """真实仓库 safety/business 不重叠、关键安全参数在册、类型不变式成立。"""
    assert param_class_check.check() == []


def test_param_class_detects_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "safety": ["reject_threshold", "acl_rules", "breaker_thresholds", "dlp_patterns", "shared"],
        "business": ["shared"],
    }
    f = tmp_path / "pc.yaml"
    f.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr(param_class_check, "_YAML", f)
    problems = param_class_check.check()
    assert any("归类冲突" in p and "shared" in p for p in problems)


def test_param_class_detects_missing_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "pc.yaml"
    f.write_text(yaml.safe_dump({"safety": [], "business": []}), encoding="utf-8")
    monkeypatch.setattr(param_class_check, "_YAML", f)
    problems = param_class_check.check()
    assert any("关键安全参数未登记" in p for p in problems)

