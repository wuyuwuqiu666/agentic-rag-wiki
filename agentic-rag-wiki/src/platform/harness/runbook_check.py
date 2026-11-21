"""runbook 完整性机检（护栏产物 / PRD §15 E10）：两类缺口即 CI 红。

`python -m src.platform.harness.runbook_check`

①**每类熔断须有处置小节**：从 `harness/breaker_rules/*.yaml` 取全部 breaker id，逐个要求在
`docs/runbooks/*.md` 里有 `## B<n>` 小节（现象→定位→复位→回滚）。新闸无 runbook＝违规。
②**每个可达入口须进总矩阵**（M4-cl）：入口清单**由代码派生、不由人手抄**——
HTTP 行取 `gateway/http.py` 的路由装饰器、CLI 行取 `gateway/cli.py` 的子命令注册位，
逐条要求在 `docs/runbooks/entry_matrix.md` 的表格首列出现且只出现一次；矩阵里引用到的处置页必须真实存在；
矩阵里多出的行＝幻影入口（文档声称有、代码没有），同样违规。

为什么②要判"首列精确相等"而不是"全页搜字符串"：路径是前缀关系的（`/api/ingest` ⊂ `/api/ingest/file`），
子串判据会让一条宽口径行糊掉整族入口——同族缺陷见 M4-bk/bl（执法粒度不足）。
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import yaml

_RULES_DIR = Path(__file__).resolve().parent / "breaker_rules"
_SRC = Path(__file__).resolve().parents[2]
_REPO = Path(__file__).resolve().parents[3]
_RUNBOOKS_DIR = _REPO / "docs" / "runbooks"
_MATRIX = _RUNBOOKS_DIR / "entry_matrix.md"
_HEADING = re.compile(r"^##\s+(B\d+)\b", re.MULTILINE)
_ROUTE = re.compile(r"@\w+\.(get|post|put|delete)\(\s*\"(/[^\"]+)\"")
_ADD_PARSER = re.compile(r"(\w+)\.add_parser\(\s*\"([^\"]+)\"")
_ADD_SUBPARSERS = re.compile(r"(?:(\w+)\s*=\s*)?(.*?)\.add_subparsers\(")
_CITED_PAGE = re.compile(r"`([\w./-]+\.md)`")


def _breaker_ids(rules_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in sorted(rules_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for item in raw if isinstance(raw, list) else [raw]:
            breaker = item.get("breaker") if isinstance(item, dict) else None
            if breaker:
                ids.add(str(breaker))
    return ids


def _documented_ids(runbooks_dir: Path) -> set[str]:
    ids: set[str] = set()
    if not runbooks_dir.is_dir():
        return ids
    for path in sorted(runbooks_dir.glob("*.md")):
        ids.update(_HEADING.findall(path.read_text(encoding="utf-8")))
    return ids


def _skeleton(line: str) -> str:
    """去掉字符串字面量内容与空行的骨架，只用于括号计数（匹配仍用原文）。"""
    buf: list[str] = []
    quote: str | None = None
    idx = 0
    while idx < len(line):
        ch = line[idx]
        if quote:
            if ch == "\\":
                idx += 2
                continue
            if ch == quote:
                quote = None
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch != "#":
            buf.append(ch)
        else:
            break
        idx += 1
    return "".join(buf)


def _logical_lines(text: str) -> list[str]:
    """把跨行续写的注册语句合成一条逻辑行（`x = sub.add_parser(\n "name",\n)` 是一处注册位）。

    逐物理行匹配会漏掉这类入口——漏检＝矩阵少一条也没人报警，正是本门要防的那类缺口。
    """
    lines: list[str] = []
    buf = ""
    depth = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        buf = f"{buf} {stripped}" if buf else stripped
        delta = _skeleton(raw).count("(") - _skeleton(raw).count(")")
        depth += delta
        if depth <= 0:
            lines.append(buf)
            buf = ""
            depth = 0
    if buf:
        lines.append(buf)
    return lines


def _http_entries(text: str) -> list[str]:
    return [f"{m.upper()} {p}" for m, p in _ROUTE.findall(text)]


def _cli_entries(text: str) -> tuple[list[str], list[str]]:
    """由子命令注册位派生 CLI 入口串（`cli ask` / `cli admin reset`），返回（入口, 判据违规）。

    变量名→命令前缀的映射来自 `add_subparsers` 的归属，分两趟建：先认"整行只定义子命令组"的
    顶层变量（`sub = p.add_subparsers(...)` → `cli`），再消费注册位并在链式定义时补登子组前缀
    （`adm = sub.add_parser("admin").add_subparsers(...)` → `cli admin`）。顺序反了就会在第一条
    `add_parser` 上归组失败。归组不明时不猜前缀、直接返回违规（漏检比误报危险——那正是
    "矩阵看着齐、实则少一条"的成因）。
    """
    lines = _logical_lines(text)
    prefix: dict[str, str] = {}
    for line in lines:
        if _ADD_PARSER.search(line):
            continue
        m = _ADD_SUBPARSERS.search(line)
        if m and m.group(1):
            prefix.setdefault(m.group(1), "cli")
    entries: list[str] = []
    for line in lines:
        own = _ADD_PARSER.findall(line)
        if not own:
            continue
        m = _ADD_SUBPARSERS.search(line)
        var = m.group(1) if m else None
        for i, (receiver, name) in enumerate(own):
            base = prefix.get(receiver)
            if base is None:
                return entries, [f"CLI 子命令注册位无法归组：`{receiver}.add_parser({name!r})`"
                                 f"（总矩阵判据依赖注册归属，请在该行可见的父命令变量上注册）"]
            entries.append(f"{base} {name}")
            # 同一行链式定义子命令组时，该组前缀＝刚登记的这条命令
            if var and i == 0:
                prefix[var] = f"{base} {name}"
    return entries, []


def _matrix_rows(matrix: Path) -> tuple[list[str], list[str]]:
    """返回（首列入口清单, 矩阵内引用到的处置页清单）。"""
    if not matrix.is_file():
        return [], []
    first_cells: list[str] = []
    pages: list[str] = []
    for line in matrix.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cell = stripped.strip("|").split("|")[0].strip().strip("`")
            if cell and not set(cell) <= {"-", ":", " "} and cell != "入口":
                first_cells.append(cell)
        pages.extend(_CITED_PAGE.findall(line))
    return first_cells, pages


def _entry_problems() -> list[str]:
    problems: list[str] = []
    if not _MATRIX.is_file():
        return [f"可达入口总矩阵缺失：{_MATRIX}（§15 E10「每可达入口须有处置页」的单一源）"]
    documented, cited_pages = _matrix_rows(_MATRIX)
    http_text = (_SRC / "platform" / "gateway" / "http.py").read_text(encoding="utf-8")
    cli_text = (_SRC / "platform" / "gateway" / "cli.py").read_text(encoding="utf-8")
    cli_entries, cli_problems = _cli_entries(cli_text)
    problems.extend(cli_problems)
    derived = _http_entries(http_text) + cli_entries
    if cli_problems:
        return problems
    problems.extend(
        f"代码里 `{entry}` 注册了 {n} 次（门面重复挂载＝先修代码再补矩阵）"
        for entry, n in sorted(Counter(derived).items()) if n > 1
    )
    problems.extend(
        f"总矩阵重复行：`{entry}` 出现 {n} 次（同一入口两张皮＝口径分叉）"
        for entry, n in sorted(Counter(documented).items()) if n > 1
    )
    registered, documented_set = set(derived), set(documented)
    for entry in sorted(registered - documented_set):
        problems.append(f"可达入口未进总矩阵：`{entry}`（新增入口须同页登记处置页归属，见 docs/runbooks/）")
    for entry in sorted(documented_set - registered):
        problems.append(f"总矩阵有幻影入口行：`{entry}` 在代码里没有对应注册位（删该行或补真实入口）")
    for page in sorted(set(cited_pages)):
        if page == _MATRIX.name:
            continue
        if not (_RUNBOOKS_DIR / page).is_file() and not (_REPO / page).is_file():
            problems.append(f"总矩阵指路不存在的页：`{page}`")
    return problems


def check() -> list[str]:
    problems: list[str] = []
    if not _RUNBOOKS_DIR.is_dir():
        return [f"runbook 目录缺失：{_RUNBOOKS_DIR}"]
    required = _breaker_ids(_RULES_DIR)
    documented = _documented_ids(_RUNBOOKS_DIR)
    for breaker in sorted(required - documented):
        problems.append(f"{breaker} 无 runbook 小节（每个熔断须有 现象→定位→复位→回滚 一页，见 docs/runbooks/）")
    problems.extend(_entry_problems())
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("runbook_check 违规：")
        for p in problems:
            print("  -", p)
        return 1
    n = len(_breaker_ids(_RULES_DIR))
    entries = _matrix_rows(_MATRIX)[0]
    print(f"runbook_check：{n} 类熔断均有处置页（{_RUNBOOKS_DIR}）；"
          f"{len(entries)} 条可达入口全部在册总矩阵且处置页在位（{_MATRIX.name}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
