"""C 区学习回路：参数提案器（FR-MEM-3/4）。业务参数可提案自动应用；安全参数只出提案。

跨区三禁之③：安全参数 auto_applicable 恒 False，由类型不变式 + harness/param_class_check 双重执法。
"""

from __future__ import annotations

from src.platform.types import ParamProposal

# 单一事实源=param_class.yaml；此处镜像校验（漂移由 param_class_check 拦下）
_SAFETY_PARAMS = {"reject_threshold", "acl_rules", "breaker_thresholds", "dlp_patterns"}


def propose(param: str, before: float | str, after: float | str, evidence: list[str]) -> ParamProposal:
    param_class = "safety" if param in _SAFETY_PARAMS else "business"
    return ParamProposal(
        param=param,
        param_class=param_class,  # type: ignore[arg-type]
        before=before,
        after=after,
        evidence=evidence,
        status="proposed",
        auto_applicable=False,  # 执行档一律人审（M5 达标后 business 方可自动应用）
    )
