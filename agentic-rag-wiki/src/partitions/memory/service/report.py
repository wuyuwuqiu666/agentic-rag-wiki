"""C 区学习回路：提案报告渲染与格式合规校验（FR-MEM-3 影子验证产物 / FR-MEM-6 出口=报告格式合规）。

报告是"提案人审"的载体：必含 参数/参数级/before/after/依据样本/影子验证/门 七段；依据样本只放指针（落文件不入上下文，C5）。
金标尺子 `tests/eval/` 只读、不由在线反馈喂（FR-MEM-5）——单机期金标未建，影子验证记 `deferred`，任何参数都不自动应用。
"""

from __future__ import annotations

from src.platform.types import ParamProposal

_SECTIONS = ("参数", "参数级", "before", "after", "依据样本", "影子验证", "门")


def render(proposal: ParamProposal, *, shadow: str, gate: str) -> str:
    """渲染提案报告（markdown）。`shadow`=影子验证结论/路径，`gate`=生效门（人审/不自动应用）。"""
    evidence = "\n".join(f"  - {ptr}" for ptr in proposal.evidence) or "  - （无）"
    return "\n".join(
        (
            f"# 参数提案报告 · {proposal.param}",
            f"- 参数: {proposal.param}",
            f"- 参数级: {proposal.param_class}",
            f"- before: {proposal.before}",
            f"- after: {proposal.after}",
            f"- 依据样本:\n{evidence}",
            f"- 影子验证: {shadow}",
            f"- 门: {gate}",
        )
    )


def is_compliant(text: str) -> bool:
    """七段齐全且依据样本非空=格式合规（机检口径，非内容质量）。"""
    if not any("  - " in line and "（无）" not in line for line in text.splitlines()):
        return False
    labels = {line.split(":", 1)[0].lstrip("- ").strip() for line in text.splitlines() if ":" in line}
    return all(section in labels for section in _SECTIONS)
