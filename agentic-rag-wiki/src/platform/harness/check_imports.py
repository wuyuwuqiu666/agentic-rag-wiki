"""依赖方向机检（PRD §6 白/黑名单 + §6.1 跨区三禁，护栏产物 #1）。

`python -m src.platform.harness.check_imports` — 违规打印并 exit(1)，pre-commit/CI 阻断。
执法点：①分区只依赖 platform 端口/契约，禁跨区直接 import（含经 _types）
②B 区（wiki）不得 import A 区（ingest）——回写只经 providers.IngestionPort
③_types 不出区 ④SDK 实现只在 providers/*_impl（黑名单外泄即拦）⑤breaker 零 IO 纯净
⑥§6 禁令3：编排框架 langgraph/langchain 收紧到唯一落点 orchestration_langgraph.py（即便 providers/ 目录对其它 SDK 豁免，此二者越界到别处仍拦）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]  # 仓库根（含 src/）
_SRC = _ROOT / "src"

# 第一方层键：platform.<layer> 或 partition:<区>.<子层>
_PLATFORM = {"types", "config", "providers", "breaker", "gateway", "harness", "schemas"}
_SUB = {"repo", "service", "runtime", "_types"}
# P1 纯净内核：breaker 层禁用的 stdlib IO 模块（零 IO，指标入→BreakerDecision 出的纯函数）
_BREAKER_IO = {"os", "subprocess", "socket", "pathlib"}
# §6 禁令3：编排框架 SDK 只允许出现在唯一落点文件——即便整个 providers/ 目录对其它 SDK 豁免，
# langgraph/langchain 仍收紧到单文件粒度（否则"只允许出现在 orchestration_langgraph.py"只是文档空话）。
_ORCH_SDK = {"langgraph", "langchain"}
_ORCH_FILE = Path("src/platform/providers/orchestration_langgraph.py")

# 各源允许的 platform 层依赖（跨区依赖另行单列）
_PLATFORM_OK: dict[str, set[str]] = {
    "types": {"types"},
    "config": {"types", "config"},
    "providers": {"types", "providers"},
    "breaker": {"types", "breaker"},
    "harness": {"types", "config", "breaker", "harness", "schemas"},
    "gateway": _PLATFORM | {"ingest", "wiki", "memory"},  # 组合根：可触所有端口/分区实现
}
_SUB_OK: dict[str, set[str]] = {
    "_types": {"types", "config", "providers"},
    "repo": {"types", "config", "providers"},
    "service": {"types", "config", "providers", "breaker"},
    "runtime": {"types", "config", "providers", "breaker"},
}
# 区内子层链：只能引更靠内的子层
_PREDECESSOR: dict[str, set[str]] = {"_types": {"_types"}, "repo": {"_types", "repo"}, "service": {"_types", "repo", "service"}, "runtime": _SUB}


def _source_key(rel: Path) -> tuple[str, str, str] | None:
    """返回 (kind, 区/层, 子层)；kind ∈ {'platform','partition'}。"""
    parts = rel.parts
    if parts[0] != "src":
        return None
    if len(parts) >= 3 and parts[1] == "platform":
        return ("platform", parts[2], "")
    if len(parts) >= 4 and parts[1] == "partitions":
        return ("partition", parts[2], parts[3] if parts[3] in _SUB else "")
    return None


def _import_layer(mod: str) -> tuple[str, str, str] | None:
    if not mod.startswith("src."):
        return None
    seg = mod.split(".")
    if seg[1] == "platform" and len(seg) >= 3:
        return ("platform", seg[2], "")
    if seg[1] == "partitions" and len(seg) >= 4:
        return ("partition", seg[2], seg[3] if seg[3] in _SUB else "")
    return None


def _imports(tree: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def _violations(rel: Path, tree: ast.AST, blacklist: set[str]) -> list[str]:
    src = _source_key(rel)
    # P1 纯净内核：breaker 零 IO——os/subprocess/socket/pathlib 无 src 层映射（_import_layer 返 None），
    # 必须在下面 `tgt is None → continue` 之前判定，否则该检查永远命中不到 stdlib import（=死代码）。
    is_breaker = src is not None and src[0] == "platform" and src[1] == "breaker"
    v: list[str] = []
    for mod in _imports(tree):
        top = mod.split(".")[0]
        if top in blacklist and not (rel.parent.name == "providers" or rel.parent.name == "gateway"):
            v.append(f"SDK 泄漏：{rel} import {mod}（实现只允许在 providers/*_impl）")
        if is_breaker and top in _BREAKER_IO:
            v.append(f"纯净性破坏：breaker import {mod}（零 IO）")
        if top in _ORCH_SDK and rel != _ORCH_FILE:
            v.append(f"§6 禁令3 越界：编排框架 SDK {mod} 只允许出现在 {_ORCH_FILE}，实际出现在 {rel}")
        tgt = _import_layer(mod)
        if tgt is None or src is None:
            continue
        s_kind, s_zone, s_sub = src
        t_kind, t_zone, t_sub = tgt
        if s_kind == "platform":
            if t_kind == "partition":
                if s_zone != "gateway":
                    v.append(f"反向依赖：platform/{s_zone} import {mod}（仅组合根可触分区）")
            elif t_zone not in _PLATFORM_OK.get(s_zone, set()):
                v.append(f"跳层/反向：{rel} import {mod}")
        else:  # partition 源
            if t_kind == "partition":
                if t_zone != s_zone:
                    v.append(f"跨区直连：{rel} import {mod}（须经 platform 端口/契约）")
                elif t_sub and t_sub not in _PREDECESSOR.get(s_sub or "runtime", set()):
                    v.append(f"区内跳层：{rel} import {mod}")
            elif t_zone not in _SUB_OK.get(s_sub or "runtime", set()):
                v.append(f"依赖越界：{rel} import {mod}")
    return v


def check(root: Path = _SRC) -> list[str]:
    bl_path = _ROOT / "src" / "platform" / "harness" / "sdk_blacklist.txt"
    blacklist = {ln.strip() for ln in bl_path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")}
    viol: list[str] = []
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(_ROOT)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover
            viol.append(f"解析失败 {rel}: {exc}")
            continue
        viol.extend(_violations(rel, tree, blacklist))
    return viol


def main() -> int:
    viol = check()
    if viol:
        print("check_imports 违规：")
        for line in viol:
            print("  -", line)
        return 1
    print("check_imports：依赖方向 + 跨区三禁 全绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
