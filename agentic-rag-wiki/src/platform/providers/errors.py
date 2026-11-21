"""fail-loud 异常（NFR-8）：供给越界/断供必须显式抛出，禁止静默降速或降级。

全分区共用；组合根捕获后按熔断档位处置。
"""

from __future__ import annotations


class ProviderError(RuntimeError):
    """provider 层错误基类。"""


class ProviderNotConfigured(ProviderError):
    """云/本地档被选中但依赖或凭证缺失：不静默回退 dev 档，直接显式失败。"""


class VramBudgetExceeded(ProviderError):
    """本地推理 batch 越显存安全上界（NFR-8）：调用方须把 batch 降回安全档。"""

    def __init__(self, batch: int, safe_batch: int) -> None:
        super().__init__(f"batch={batch} 越安全上界 {safe_batch}（显存×{0.9} 反推），须降批重放")
        self.batch = batch
        self.safe_batch = safe_batch


class BreakerTripped(RuntimeError):
    """熔断硬断（非 shadow 且 action=block）：冻结当前写侧操作，须按 runbook 处置后复位。"""

    def __init__(self, decision: object) -> None:
        self.decision = decision
        reason = getattr(decision, "reason", "")
        super().__init__(f"熔断硬断：{reason}")
