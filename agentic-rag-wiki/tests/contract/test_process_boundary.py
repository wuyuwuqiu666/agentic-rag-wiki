"""进程外执行边界机检（T5/C2；M4-ce 1c 引入本机 Office 转换后立的锁）。

背景：`providers/office_com.py` 是**产品代码里唯一**起外部进程的站点（PowerShell → Word/PowerPoint COM）。
"执行须有边界"若只写在 docstring 里就是文档条款而非护栏（P2）。本文件把三条口径转成可跑断言：
①`subprocess` 只允许出现在 `providers/`（service/runtime/repo/types/config/breaker/gateway 出现即违规——
  端口层不感知进程，换实现不动调用方）；
②providers 里的每次 `subprocess.run(...)` 必须**带 timeout**（无界等待＝一个弹对话框的 Office 能挂死整条摄取链）；
③不得用 `shell=True`，且 argv 首位必须是**字面量可执行文件名**（命令注入面的结构性消除）。
harness 自身（`secret_scan.py`/`check_imports.py`）确实要跑外部命令，不在本锁范围内——它是治理层、不是被执行的产品链。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "platform"
_PROCESS_MODULES = {"subprocess"}
# 产品代码里允许起进程的目录（相对 src/platform）；其余一律违规。
_ALLOWED_PREFIX = "providers"


def _python_files() -> list[Path]:
    return [p for p in sorted(_SRC.rglob("*.py")) if "harness" not in p.parts and "__pycache__" not in p.parts]


def _rel(path: Path) -> str:
    return path.relative_to(_SRC).as_posix()


def _imported_process_modules(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: _rel(p))
def test_only_providers_may_spawn_processes(path: Path) -> None:
    used = _imported_process_modules(ast.parse(path.read_text(encoding="utf-8"))) & _PROCESS_MODULES
    rel = _rel(path)
    if used and not rel.startswith(_ALLOWED_PREFIX + "/"):
        pytest.fail(f"{rel} 起了进程外执行（{sorted(used)}）——执行边界只在 providers/ 落点，其余层须经端口")


def _subprocess_calls(tree: ast.Module) -> list[ast.Call]:
    """只认 `subprocess.run/check/check_output/Popen/call(...)`——普通同名方法（如 GraphPort.run）不算。"""
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "subprocess"
            and f.attr in {"run", "check", "check_output", "Popen", "call"}
        ):
            out.append(node)
    return out


def _keyword(call: ast.Call, key: str) -> ast.keyword | None:
    return next((kw for kw in call.keywords if kw.arg == key), None)


@pytest.mark.parametrize("path", sorted((_SRC / _ALLOWED_PREFIX).glob("*.py")), ids=lambda p: _rel(p))
def test_provider_spawn_sites_are_bounded(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for call in _subprocess_calls(tree):
        if _keyword(call, "shell") is not None:
            pytest.fail(f"{_rel(path)} 的进程调用带 shell= ——脚本体常量化＋argv 直传才是本档口径")
        if _keyword(call, "timeout") is None:
            pytest.fail(f"{_rel(path)} 的进程调用缺 timeout= ——无界等待能把整条摄取链挂死")


def test_office_converter_launches_a_literal_executable() -> None:
    """argv 首位必须是字面量 `"powershell"`，路径只走 env 传入（不进拼接）。"""
    src = (_SRC / _ALLOWED_PREFIX / "office_com.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    argvs = [call.args[0] for call in _subprocess_calls(tree) if call.args]
    assert argvs, "office_com.py 应至少有一处进程调用"
    for argv in argvs:
        assert isinstance(argv, (ast.List, ast.Tuple)), "argv 必须是字面量序列，不得运行时拼接"
        first = argv.elts[0]
        assert isinstance(first, ast.Constant) and first.value == "powershell", "可执行文件名必须是字面量 powershell"
    for node in ast.walk(tree):  # 输入输出路径不得拼进命令串
        if isinstance(node, ast.JoinedStr):
            assert "powershell" not in ast.unparse(node), "命令串里出现 powershell＝路径被拼进了命令行"
