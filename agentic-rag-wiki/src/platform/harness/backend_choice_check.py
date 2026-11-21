"""§9/§11 护栏：装配表后端选择完整性 + 云档×预检接线（防"幻影后端/静默错配/云档漏检"回归）。

缺陷家族：`ProviderChoices` 某后端字段的 `Literal` 域里列了档位 `"s3"`/`"kafka"`/`"langgraph"`/`"cloud"` 等，
但组合根 `composition.py` **未为该值建分支**——选了它却走 `else` 静默落到默认档（用户以为接了企业 S3/Kafka/LangGraph/云
rerank，实拿 filesystem/pg_queue/plain/dev）。本会话此家族连犯 5 次（M4-an pgvector / M4-ap langgraph / M4-aq s3·kafka /
M4-ar rerank-cloud），点修不胜枚举→ 固化为可机检产物（P2），锁死单向不变式：

  **`ProviderChoices` 每个后端字段的每个"非默认"枚举值，必须在 `composition.py` 有显式 `c.<field> == "<value>"` 分支。**

设计取舍（同 `config_surface_check`/`lifecycle_check` 口径）：
- **不实例化 `Settings`/`ProviderChoices`**（env 云凭证 `model_validator` 会 fail-fast，护栏只读结构）——AST 解析域与默认值。
- **默认值豁免**：字段的默认档由 `else` 兜底合法（如 embedding 默认 `dev_hash`、vectorstore 默认 `memory`），不要求其显式出现。
- 仅执法 `ProviderChoices`（装配表），**不扫 `Settings.env`**——env(dev/staging/prod) 依 E2 为"零分支配置面"，扫它会误报。
- 分支识别采用规范化源码子串 `c.<field>=="<value>"`（组合根统一用 `==` 比较；如改字典查表需同步本护栏口径）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SETTINGS = _REPO / "src" / "platform" / "config" / "settings.py"
_COMPOSITION = _REPO / "src" / "platform" / "gateway" / "composition.py"


def _literal_values(sub: ast.Subscript) -> list[str] | None:
    """`Literal["a","b"]` → ["a","b"]；非 Literal 或含非字符串项 → None。"""
    value = sub.value
    if not (isinstance(value, ast.Name) and value.id == "Literal"):
        return None
    sl = sub.slice
    if not isinstance(sl, ast.Tuple):
        return None
    out: list[str] = []
    for elt in sl.elts:
        if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
            return None
        out.append(elt.value)
    return out


def _backend_fields(tree: ast.Module) -> dict[str, tuple[list[str], str]]:
    """取 `ProviderChoices` 各字段 → (枚举域, 默认值)。别名 `X=Literal[...]` 在模块级解析。"""
    aliases: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Subscript):
            vals = _literal_values(node.value)
            if vals is not None:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        aliases[tgt.id] = vals
    out: dict[str, tuple[list[str], str]] = {}
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == "ProviderChoices"):
            continue
        for st in node.body:
            if not (isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)):
                continue
            domain: list[str] | None = None
            if isinstance(st.annotation, ast.Name):
                domain = aliases.get(st.annotation.id)
            elif isinstance(st.annotation, ast.Subscript):
                domain = _literal_values(st.annotation)
            if not domain or not isinstance(st.value, ast.Constant) or not isinstance(st.value.value, str):
                continue
            out[st.target.id] = (domain, st.value.value)
    return out


def _settings_surface(tree: ast.Module, source: str) -> tuple[set[str], dict[str, str]]:
    """取 Settings 字段名集 + 预检方法源码段（键=方法名）。只读结构，不实例化。"""
    fields: set[str] = set()
    methods: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == "Settings"):
            continue
        for st in node.body:
            if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                fields.add(st.target.id)
            elif isinstance(st, ast.FunctionDef) and st.name in (
                "_require_credentials_for_cloud",
                "_egress_offenders",
            ):
                methods[st.name] = re.sub(r"\s+", "", ast.get_source_segment(source, st) or "")
    return fields, methods


def _cloud_wiring_problems(fields: dict[str, tuple[list[str], str]], settings: ast.Module, source: str) -> list[str]:
    """云档接线完整性：可选 "cloud" 后端的每个装配字段，Settings 须有凭证字段且两预检均消费之。

    家族=本会话连犯 2 次的"新增云后端漏扩启动预检"（M4-ar 加 rerank=cloud 后，M4-as 补出网白名单、
    本轮补凭证校验）：预检按 llm/embedding 手写枚举、加第三档必漏——改为对 `ProviderChoices` 域动态核对。
    """
    sf, methods = _settings_surface(settings, source)
    problems: list[str] = []
    for field, (domain, _) in sorted(fields.items()):
        if "cloud" not in domain:
            continue
        base, key = f"cloud_{field}_base_url", f"cloud_{field}_api_key"
        for name in (base, key):
            if name not in sf:
                problems.append(f"云档 {field}={domain!r} 可选，但 Settings 缺凭证字段 {name}")
        for meth in ("_require_credentials_for_cloud", "_egress_offenders"):
            norm = methods.get(meth)
            if norm is None:
                problems.append(f"Settings.{meth} 不存在：云档 {field} 的启动预检失去落点")
            elif f"self.{base}" not in norm:
                problems.append(
                    f"云档 {field}=\"cloud\" 未被 Settings.{meth} 消费（缺 `self.{base}` 引用）："
                    f"选此档可绕过启动即 fail-fast 的凭证/出网白名单预检（T1/R11），须同步扩预检"
                )
    return problems


def check() -> list[str]:
    problems: list[str] = []
    if not _SETTINGS.is_file():
        return [f"找不到 settings.py：{_SETTINGS}（护栏失效须修）"]
    if not _COMPOSITION.is_file():
        return [f"找不到 composition.py：{_COMPOSITION}（护栏失效须修）"]
    source = _SETTINGS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fields = _backend_fields(tree)
    norm = re.sub(r"\s+", "", _COMPOSITION.read_text(encoding="utf-8"))
    for field, (domain, default) in sorted(fields.items()):
        for value in domain:
            if value == default:
                continue  # 默认档由 else 兜底，合法豁免
            if f'c.{field}=="{value}"' not in norm:
                problems.append(
                    f"ProviderChoices.{field} 声明后端 {value!r}（默认={default!r}），但 composition.py 无 `c.{field} == \"{value}\"` "
                    f"分支：选此档将静默落默认档=幻影后端/装配错配，须补 fail-loud 占位分支或删枚举值"
                )
    problems.extend(_cloud_wiring_problems(fields, tree, source))
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("backend_choice_check：装配表后端选项↔组合根分支漂移", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    n = len(_backend_fields(ast.parse(_SETTINGS.read_text(encoding="utf-8"))))
    print(
        f"backend_choice_check：{n} 个后端字段的非默认枚举值均有显式分支、云档×凭证/出网两预检接线齐，"
        "无幻影后端/静默错配/云档漏检。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
