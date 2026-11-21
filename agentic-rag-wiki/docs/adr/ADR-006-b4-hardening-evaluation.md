# ADR-006：B4 注入/外泄熔断——M4 转硬断评估（结论=维持 shadow 至企业档）

- 状态：采纳（评估结论）
- 日期：2026-09-28
- 关联：§13 M4「B4 转硬断评估」；`harness/breaker_rules/b4.yaml`（`level: shadow`）；F2 回写 ACL 交集；跨区三禁②；R9 复位疲劳。

## 背景
B4（注入与外泄探测：注入特征命中 / DLP 模式 / 越权引用）v1 起为 shadow 档（只记不拦，`IngestPipeline` 隔离路径经 `GuardPort.check("injection_score", 隔离占比, "B4")` 观察）。M4 要求评估是否转硬断（越阈直接阻断摄取/发布）。

## 评估判据（硬断须同时满足）
1. 有**足量真实注入/外泄样本**校准 `injection_score` 的 soft/hard 阈，使硬断的假阳率可控；
2. 有**误拦成本基线**（正常语料被误判占比），证明硬断不至于制造复位疲劳（R9）；
3. 硬断后的**双人复位 runbook** 就位。

## 结论：单机执行档维持 shadow，不转硬
- 执行档=单机演示/合成语料，**真密级语料不入本机云出口**（§10 / T 红线），上述判据①②不可自证——在无真实分布的情况下硬断，只会用未知假阳率去拦正常摄取，得不偿失。
- 现有缓解已覆盖主要风险：命中即 `quarantine`（隔离送审、不入索引）+ C 区审计留痕 + 回写块 ACL=源交集收窄（F2）+ 越权引用读侧 pre-filter 兜底。shadow 期这些已在生效，唯不"硬断流水线"。
- **转硬断的前置**：企业档启用、真实密级语料进入受控本地装配域、积累 ≥2 周真实 `injection_score` 分布后，按判据①②重定阈并翻 `b4.yaml level: hard`。

## 可逆性与代价
- 开关是**纯配置**（`b4.yaml` level + `Settings.shadow_breakers`），代码零改动——执法点（隔离路径 + GuardPort）已就位。
- 代价：shadow 期漏放的注入到索引的可能，由 quarantine + ACL 交集 + 读侧 pre-filter 三重兜底降低爆炸半径；可事后按审计批量 retract。
