"""C 区学习回路运行时（FR-MEM-3 提案人审 / FR-MEM-6 启用时点，单机=演练模式）。

链路：合成信号快照 → 冷启动守卫（小样本禁调参）→ `param_proposal.propose` 出提案 → 影子验证（金标未建=deferred）
→ 渲染报告落文件 → **一律停在 proposed**（执行期单机不自动应用任何参数，安全参数更由类型不变式禁自动生效）。
真·影子验证（固定金标回放 + 小流量 A/B）与 M5 转正决策在企业档启用时接；本模块零跨区 import、只经 types 契约。
"""

from __future__ import annotations

from pathlib import Path

from src.partitions.memory._types import SignalSnapshot
from src.partitions.memory.service import param_proposal, report
from src.platform.types import ParamProposal

_MIN_SAMPLES = 100  # FR-MEM-6 冷启动：信号不足即"拿噪声当趋势"，禁出提案
_LOWCONF_HI = 0.30  # 低置信占比过高→建议加宽上下文（业务参数）
_REFUSE_ABNORMAL = 0.60  # 拒答率异常高→建议下调拒答阈（安全参数，仅提案）

_SHADOW_DEFERRED = "影子验证：金标评测集未建→deferred；单机演练不自动应用（FR-MEM-6）"
_GATE_BUSINESS = "门：提案人审（单机期不自动应用业务参数）"
_GATE_SAFETY = "门：仅提案（安全参数人工批准才生效，FR-MEM-4）"


class LearningLoop:
    def __init__(self, outdir: str = "workspace/proposals") -> None:
        self._dir = Path(outdir)

    def run(self, snapshot: SignalSnapshot) -> list[ParamProposal]:
        """据信号快照产出提案（含落盘报告）。冷启动或信号正常→空列表。"""
        if snapshot.sample_size < _MIN_SAMPLES:
            return []
        proposals: list[ParamProposal] = []
        if snapshot.refusal_rate >= _REFUSE_ABNORMAL:
            proposals.append(
                param_proposal.propose("reject_threshold", before=0.35, after=0.30, evidence=list(snapshot.evidence))
            )
        if snapshot.refusal_rate < _REFUSE_ABNORMAL and snapshot.low_conf_rate >= _LOWCONF_HI:
            proposals.append(
                param_proposal.propose("answer_topn", before=8, after=12, evidence=list(snapshot.evidence))
            )
        for prop in proposals:
            self._finalize(prop)
        return proposals

    def _finalize(self, prop: ParamProposal) -> None:
        """出报告、落文件、状态钉死 proposed（演练期不 shadow_verified→applied 的任何自动推进）。"""
        gate = _GATE_SAFETY if prop.param_class == "safety" else _GATE_BUSINESS
        text = report.render(prop, shadow=_SHADOW_DEFERRED, gate=gate)
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{prop.param}.md"
        path.write_text(text, encoding="utf-8")
        prop.shadow_report = str(path)
        prop.status = "proposed"
