"""§9/§11 护栏：配置面双向单一口径（模板键⇒字段 且 字段⇒消费者，防两类"幻影配置"回归）。

缺陷家族①（声明却零消费，M4-al/am/an 点修 5 枚）：`.env.example` 广告 `APP_FOO=` 键但 `Settings` 无字段——
`extra="ignore"` 下用户填了被静默丢弃。锁单向不变式 **模板声明面 ⊆ Settings 字段面**。
缺陷家族②（字段存在却无人读，M4-aj/an/ap/aq 点修 5+ 枚旋钮）：`Settings`/`ProviderChoices` 字段在全 src
无消费者=下一枚幻影旋钮（加字段忘接）。锁反向不变式 **字段面 ⊆ 消费面**（消费=属性访问/Name/按名取值
字符串常量三形态；settings.py 内 Validator/Property 自读合法计入；豁免须附理由且防腐测试防僵尸豁免）。

设计取舍（同 lifecycle_check/breaker validate 口径）：
- **不实例化 `Settings`**（env 云凭证校验会在装配期 fail-fast，护栏只读结构不触发它）——AST 解析字段名。
- "字段未进模板"**不报**——执行档调参（agent_max_hops/rrf_k/reject_threshold 等）依 §8.8 由代码默认值
  定档、无须全数进模板，强求反而制造噪声。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SETTINGS = _REPO / "src" / "platform" / "config" / "settings.py"
_SRC = _REPO / "src"
_ENV_EXAMPLE = _REPO / ".env.example"

_KEY_RE = re.compile(r"^\s*(#?)\s*(APP_[A-Z0-9_]+)\s*=")

# 有意零业务读取的豁免字段（名→理由）。新增豁免必须附测试锚，否则等于给自己开洞。
_EXEMPT_FIELDS: dict[str, str] = {
    "env": (
        "E2 零分支 profile 标签：业务代码按 E2 设计禁止读它分支；消费面=pydantic Literal 对非法 "
        "APP_ENV 启动即拒 + tests/smoke/test_config_profiles.py 三档快照锁'除 env 外零差异'"
    ),
}


def _settings_fields(path: Path) -> set[str]:
    """AST 取 Settings 顶层字段 + ProviderChoices 字段（映射为 `providers__<name>` 供嵌套 env 键比对）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fields: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name not in ("Settings", "ProviderChoices"):
            continue
        for st in node.body:
            if not isinstance(st, ast.AnnAssign) or not isinstance(st.target, ast.Name):
                continue
            name = st.target.id
            if node.name == "Settings":
                fields.add(name)
            else:
                fields.add(f"providers__{name.lower()}")
    return fields


def _env_keys(path: Path) -> list[tuple[str, str]]:
    """返回 (env 键, 是否注释行)——含被注释的样板键（同为模板声明意图）。"""
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _KEY_RE.match(line)
        if m:
            out.append((m.group(2), "#" if m.group(1) else ""))
    return out


def to_field(key: str) -> str:
    """`APP_PROVIDERS__LLM` → `providers__llm`；`APP_LOG_LEVEL` → `log_level`。"""
    return key[len("APP_") :].lower()


def _declared_fields(tree: ast.Module) -> tuple[set[str], set[int]]:
    """(Settings∪ProviderChoices 字段名集, settings.py 内这些字段声明位 Name 的 id)。"""
    names: set[str] = set()
    targets: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in ("Settings", "ProviderChoices"):
            for st in node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    names.add(st.target.id)
                    targets.add(id(st.target))
    return names, targets


def _consumed_names(tree: ast.Module, decl_targets: set[int]) -> set[str]:
    """一文件"消费"某名的三形态：属性访问 x.<name>、非声明位 Name、按名取值字符串常量（model_fields[...]）。"""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Name) and id(n) not in decl_targets:
            out.add(n.id)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out


def _phantom_field_problems() -> list[str]:
    """反向不变式：Settings/ProviderChoices 每字段必有真实消费者，否则=幻影旋钮（声明却无人读）。"""
    fields: set[str] = set()
    consumed: set[str] = set()
    for py in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        names, targets = _declared_fields(tree)
        fields |= names
        consumed |= _consumed_names(tree, targets if py == _SETTINGS else set())
    return [
        f"Settings/ProviderChoices 字段 {name!r} 在 src/ 零消费：幻影旋钮（声明却无人读取）。"
        f"转真实消费者或删字段；确属框架级语义（如 E2 零分支 profile 标签）须入 _EXEMPT_FIELDS 并写明理由+测试锚"
        for name in sorted(fields - consumed - set(_EXEMPT_FIELDS))
    ]


def check() -> list[str]:
    problems: list[str] = []
    if not _SETTINGS.is_file():
        return [f"找不到 settings.py：{_SETTINGS}（护栏失效须修）"]
    if not _ENV_EXAMPLE.is_file():
        return [f"找不到 .env.example：{_ENV_EXAMPLE}（护栏失效须修）"]
    fields = _settings_fields(_SETTINGS)
    for key, _commented in _env_keys(_ENV_EXAMPLE):
        if to_field(key) not in fields:
            problems.append(
                f".env.example 声明 {key} 但 Settings 无对应字段（{to_field(key)}）："
                "extra=ignore 下用户填了静默无效=幻影配置，须补字段或删模板键"
            )
    problems.extend(_phantom_field_problems())
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("config_surface_check：配置面漂移（模板键缺字段 或 字段零消费幻影）", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(
        f"config_surface_check：模板 {len(_env_keys(_ENV_EXAMPLE))} 键全映射到 Settings 字段，"
        "且各字段有真实消费者（豁免除外），双向无幻影配置。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
