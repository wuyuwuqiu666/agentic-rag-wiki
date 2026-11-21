# ADR-003：B0 总闸——冻结写侧、保读侧、双人复位

- 状态：采纳
- 决策：连续未复位触发数达阈（默认 3）→ B0 拉闸，冻结全部写侧、保留读侧，复位须双人填理由并入审计（`workspace/audit/` append-only）。触发分升级/失效/人工三类（v0.6 分级）。
- 后果：`harness/breaker_rules/b0.yaml` + `platform/breaker/engine.evaluate_b0` 落地；防"复位疲劳"，复位不静默。
