"""CLI 命令名＝可达入口的一部分：文档教的每条命令必须真能敲（审计 #9／M4-ct）。

病根不是某一行写错，而是**同一个入口有两种写法**：`pyproject` 无 `[project.scripts]`（也
无 build-system ⇒ 不可 `pip install`），所以 `agentic-rag admin reset …` 在现实里**跑不通**；
而 `argparse` 的 `prog` 当时正是这个短名，于是 usage/报错行与 runbook 都跟着抄。处置页是"照抄就要能跑"
的文档，一条跑不通的命令＝冻结期找不到复位入口。

三件事钉成机检（不靠人自觉）：

1. **`prog` 是可照抄形式**——`python -m <真实存在的模块>`，或在 `pyproject` 里真有同名 console script；
2. **文档里出现的每条命令的动词路径必须在真实 parser 里注册，且其参数名必须真被该子命令接受**（防"文档教了个不存在的子命令／敲错的开关"）；
3. **文档里不许出现不可执行的短名命令**——将来若补了打包与 `[project.scripts]`，本条自动放行，无需改测试；
4. **报错行前缀与 `prog`、处置页同一口径**——人会抄命令名的三处（usage／运行期报错／文档）分叉即病根复发。

与既有护栏的分工：`harness/runbook_check.py` 管"代码注册位 ⊆ `entry_matrix.md`"（入口有没有处置页）；
本文件管反方向"文档写的命令 ⊆ 代码里真能跑的"，且覆盖矩阵之外的 runbook/AGENTS 正文。
"""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.gateway.cli import _build_parser

_REPO = Path(__file__).resolve().parents[2]
_MODULE = "src.platform.gateway.cli"
_SHORT_NAME = "agentic-rag"

# 反引号代码段＝"照抄区"：运维真的会整段敲下去，所以动词路径**和参数名**都在这里核
_CODE_SPAN = re.compile(r"`([^`]*)`")
_MODULE_PREFIX = f"python -m {_MODULE}"
_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
# 不可执行的短名命令：`agentic-rag <verb> …`
_SHORT_CMD = re.compile(rf"(?<![\w.-]){re.escape(_SHORT_NAME)}((?:\s+[A-Za-z][A-Za-z0-9_-]*){{1,2}})")


def _command_map(parser: argparse.ArgumentParser) -> dict[tuple[str, ...], argparse.ArgumentParser]:
    """从真实 parser 递归收集 动词路径→子解析器，如 ("admin","reset")→parser——判据来源＝代码，不由人手抄。"""
    found: dict[tuple[str, ...], argparse.ArgumentParser] = {}

    def walk(actions: list[argparse.Action], prefix: tuple[str, ...]) -> None:
        for action in actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    path = (*prefix, name)
                    found[path] = sub
                    walk(sub._actions, path)  # noqa: SLF001 - 遍历子命令树只能读内部 actions

    walk(parser._actions, ())  # noqa: SLF001 - 同上
    return found


def _registered_commands(parser: argparse.ArgumentParser) -> set[tuple[str, ...]]:
    return set(_command_map(parser))


def _console_script_names() -> set[str]:
    pyproject = _REPO / "pyproject.toml"
    if not pyproject.exists():
        return set()
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return set(data.get("project", {}).get("scripts", {}))


def _markdown_docs() -> list[Path]:
    skip = {"node_modules", "dist", "__pycache__", ".venv"}
    files = [f for f in sorted(_REPO.rglob("*.md")) if not skip & set(f.parts)]
    assert files, "仓库里一个 md 都没有＝扫描器失效，不许护栏假绿"
    return files


def _doc_commands() -> list[tuple[str, int, list[str]]]:
    """把全仓 md 反引号代码段里的命令提及摊平成 (相对路径, 行号, 模块之后的 token 列表)。

    只认反引号段＝只认"人真会整段照抄的地方"；以 `…`/中文实参开头的段不计（不是动词路径）。
    """
    out: list[tuple[str, int, list[str]]] = []
    for md in _markdown_docs():
        rel = str(md.relative_to(_REPO)).replace("\\", "/")
        for line_no, line in enumerate(md.read_text(encoding="utf-8").splitlines(), start=1):
            for span in _CODE_SPAN.findall(line):
                if not span.startswith(_MODULE_PREFIX):
                    continue
                tokens = span.split()[3:]
                if tokens and _WORD.match(tokens[0]):
                    out.append((rel, line_no, tokens))
    return out


def test_prog_is_a_runnable_invocation_or_a_declared_console_script() -> None:
    """self-help 打的命令名必须等于人真能敲的那条（usage 行最容易被抄走）。"""
    prog = _build_parser().prog
    tokens = prog.split()
    if tokens[:2] == ["python", "-m"]:
        module = tokens[2].replace(".", "/")
        assert (_REPO / f"{module}.py").exists() or (_REPO / module).is_dir(), (
            f"prog 指向不存在的模块：{prog}"
        )
        return
    assert prog in _console_script_names(), (
        f"prog＝{prog!r} 既不是 `python -m …`，也没在 pyproject [project.scripts] 里声明＝不可执行"
    )


def test_registry_is_non_empty_and_holds_the_reset_entry() -> None:
    """防扫描器自身失效（M4-cl 的"0 条即假绿"教训）：已知锚点必须在册。"""
    paths = _registered_commands(_build_parser())
    assert ("ask",) in paths
    assert ("admin", "reset") in paths, "复位是硬断处置的唯一可达入口，注册位一丢本文件全线失明"
    assert len(paths) >= 10, f"命令树只剩 {len(paths)} 条，怀疑遍历逻辑被改坏"


def test_every_documented_cli_command_exists_in_the_parser() -> None:
    """正向：文档教的每条命令都真能跑（命令 ⊆ 代码）。"""
    paths = _registered_commands(_build_parser())
    top_level = {p[0] for p in paths}
    offenders: list[str] = []
    mentions = _doc_commands()
    for rel, line_no, tokens in mentions:
        head = tokens[0]
        if head not in top_level:
            offenders.append(f"{rel}:{line_no} → `{head}` 未注册")
            continue
        has_children = any(len(p) > 1 and p[0] == head for p in paths)
        if len(tokens) < 2 or not has_children or not _WORD.match(tokens[1]):
            continue  # 该命令无子命令面，或文档只写到这一层——不越位猜测其后的位置参数
        if (head, tokens[1]) not in paths:
            offenders.append(f"{rel}:{line_no} → `{head} {tokens[1]}` 未注册")
    assert len(mentions) >= 4, f"只扫到 {len(mentions)} 条命令提及，怀疑正则失效"
    assert not offenders, "文档教了跑不通的命令：\n" + "\n".join(offenders)


def _resolve_path(tokens: list[str], commands: dict[tuple[str, ...], argparse.ArgumentParser]) -> tuple[str, ...] | None:
    """文档 token 序列能对上最长的已注册动词路径；对不上（如 `admin --confirm`）返回 None 或父节点。"""
    best: tuple[str, ...] | None = None
    for n in range(1, len(tokens) + 1):
        cand = tuple(tokens[:n])
        if cand not in commands:
            break
        best = cand
    return best


def test_every_documented_cli_flag_is_accepted_by_the_parser() -> None:
    """动词路径对上还不够：参数名敲错一样是 command not usable（执法粒度，M4-ct）。
    本条就是把 `--kinds`（真实参数叫 `--kind`）这一类"看着像"的文档写法钉住的——改判据前先改代码。
    """
    commands = _command_map(_build_parser())
    root_opts = {o for a in _build_parser()._actions for o in a.option_strings}  # noqa: SLF001  （--help 之类挂在根上）
    offenders: list[str] = []
    checked = 0
    for rel, line_no, tokens in _doc_commands():
        path = _resolve_path(tokens, commands)
        if path is None:
            continue  # 动词路径本身就错，由上一条测试报错，这里不重复计
        if any(len(p) > len(path) and p[: len(path)] == path for p in commands):
            continue  # 文档没写到叶子，参数该归哪个子命令无从判定
        parser = commands[path]
        allowed = {o for a in parser._actions for o in a.option_strings} | root_opts  # noqa: SLF001
        for token in tokens[len(path):]:
            if token.startswith("--"):
                checked += 1
                if token not in allowed:
                    offenders.append(f"{rel}:{line_no} → `{' '.join(path)} {token}` 参数不存在")
    assert checked >= 6, f"只核对到 {checked} 个文档参数，怀疑摊平逻辑失效"
    assert not offenders, "文档教了跑不通的参数：\n" + "\n".join(offenders)


def test_error_and_usage_lines_use_the_same_runnable_invocation(tmp_path, capsys) -> None:
    """三口径同一份：文档正文、argparse usage（`prog`）、运行期报错前缀。

    人会抄走命令名的地方就是这三处，任何一处写短名＝把 #9 的病灶留在机器输出里。
    这里取一个**真实失败**（双人签署人相同）来验报错行，不用伪造异常。
    """
    from src.platform.gateway.cli import _INVOKE, main

    prog = _build_parser().prog
    assert prog == _INVOKE, f"报错前缀与 usage 分叉：{prog!r} ≠ {_INVOKE!r}"
    assert f"{prog} admin reset" in (_REPO / "docs/runbooks/breakers.md").read_text(encoding="utf-8"), (
        "复位处置页的命令名与 usage 不同一行口径"
    )
    rc = main(
        ["admin", "reset", "--breaker", "B1", "--operator", "sre", "--approver", "sre", "--reason", "同类"],
        container=build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"))),
    )
    err = capsys.readouterr().err
    assert rc == 1 and err.startswith(f"{_INVOKE}：ValueError"), err


def test_no_doc_teaches_an_unexecutable_short_command() -> None:
    """反向：短名 `agentic-rag …` 只有当真有同名 console script 时才允许出现在文档里。"""
    if _SHORT_NAME in _console_script_names():
        return  # 打包已落地＝该写法成立
    offenders: list[str] = []
    for md in _markdown_docs():
        rel = str(md.relative_to(_REPO)).replace("\\", "/")
        for line_no, line in enumerate(md.read_text(encoding="utf-8").splitlines(), start=1):
            for raw in _SHORT_CMD.findall(line):
                offenders.append(f"{rel}:{line_no} → `{_SHORT_NAME}{raw}`")
    assert not offenders, (
        "文档教了不可执行的短名命令（无 [project.scripts]，照抄即 command not found）：\n"
        + "\n".join(offenders)
        + f"\n两种合法收口：改写成 `python -m {_MODULE} …`，或补 build-system + [project.scripts]。"
    )
