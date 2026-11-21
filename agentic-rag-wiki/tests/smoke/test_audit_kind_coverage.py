"""护栏：审计 kind 覆盖不变式（P2/P4，M4-y）。

`AuditEvent.kind` 是 E3 冻结 Literal=事件契约全集，但"声明的 kind"不等于"执行档会触发的 kind"。
本测锁定二者关系，防两类静默漂移：
①新增/漏接：往 Literal 加成员却既无 emit 站点、又未显式登记为"故意不触发"→红；
②误自动生效：把"故意不触发"的 kind（如 param_change）在 src 里悄悄 emit（=等同自动调参越红线）→红。
即"我们知道哪些声明事件离线不发、且为什么"这一诚实声明（AGENTS §8 放行项⑩）由机检兜住、不靠自觉。
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

from src.platform.types import AuditEvent

_SRC = Path(__file__).resolve().parents[2] / "src"

# 声明但执行档故意不触发的 kind：param_change=FR-MEM-3 "生效"步（提案经影子验证+人批→变更 Git 留痕→回滚），
# 单机档"不自动应用任何参数"故不 emit（见 §13/AGENTS 放行项⑩）；随企业档 M5 转正/金标就位落地时从此表移除。
INTENTIONALLY_UNEMITTED = {"param_change"}


def _declared_kinds() -> set[str]:
    return set(get_args(AuditEvent.model_fields["kind"].annotation))


def _src_text() -> str:
    return "\n".join(py.read_text(encoding="utf-8") for py in _SRC.rglob("*.py"))


def _emitted_kinds() -> set[str]:
    # 精确按 `kind="<declared>"` 字面量匹配，避免误捕他模型的同名字段（如 ChangeEvent kind="delete"）
    text = _src_text()
    return {k for k in _declared_kinds() if f'kind="{k}"' in text}


def test_every_declared_kind_is_emitted_or_allowlisted() -> None:
    gap = _declared_kinds() - _emitted_kinds() - INTENTIONALLY_UNEMITTED
    assert not gap, f"声明的审计 kind 既无 emit 站点又未登记故意不触发：{sorted(gap)}"


def test_allowlisted_kind_actually_unemitted() -> None:
    leaked = INTENTIONALLY_UNEMITTED & _emitted_kinds()
    assert not leaked, f"故意不触发的 kind 在 src 出现 emit 站点（越红线/需移除登记）：{sorted(leaked)}"


def test_declaration_covers_full_literal() -> None:
    # 护栏自身别漏项：allowlist 必须是 declared 的子集（防登记一个已不存在/拼错的 kind 让不变式空转）
    assert _declared_kinds() >= INTENTIONALLY_UNEMITTED

