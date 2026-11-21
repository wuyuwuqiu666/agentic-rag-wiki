# ADR-008：切块策略契约与 Contextual Retrieval 默认关闭

- 状态：采纳（2026-09-23，用户批复）
- 关联：FR-ING-7（切块契约/profile 注册表）、R1（切块质量风险）、§12 生成后门、`partitions/ingest/service/chunker.py`。
- 说明：本决策原于 PRD 早期草稿被草编为"ADR-003"，与权威 `docs/adr/` 序列（ADR-003=B0 总闸）冲突；M4-s 编号追溯统一时归位为独立条目 ADR-008，内容口径与原批复一致，不改决策本身。

## 背景
FR-ING-7 立项前做云厂商（NVIDIA/AWS/Azure/Google/Databricks）× 开源（RAGFlow/docling/LlamaIndex/Chonkie/LangChain）双路调研；Chunk 成为 types 层契约字段，按 canonical 契约变更规矩留痕。

## 决策
切块=无状态纯函数 + profile 注册表；结构识别三级漏斗（规则→版面小模型→LLM 兜底）；表格原子化；overlap 默认 0 用邻居回取；**Contextual Retrieval 默认关闭**；late chunking 仅策略位。

## 理由（CR 关闭的三击穿）
LLM 生成的文档级前缀改写 chunk 文本 → ①哈希去重失效、②语义缓存键漂移（FR-CAP-1 的 ACL 指纹键连带重写）、③CDC 放大（改一段→全文档 chunk 前缀连锁重生成）。业界 -49% 失败率的收益真实，但与本系统的增量/缓存/去重机制结构性冲突；金标评测（FR-MEM-5）在 M5 有裁决权。

## 代价与对冲
无 CR 的上下文断裂损失由 breadcrumb 前缀（零成本、不改 chunk 文本哈希之外的稳定部分——注：breadcrumb 属元数据不参与内容哈希）+ 邻居回取部分补偿。

## 可逆性
CR 开关为 config 级 profile 参数，重启用需过金标回放 + ADR 更新；overlap 同理为 profile 参数。

## 落地边界（离线 vs 放行）
- 已落地（离线代码路径 + 单测）：无状态纯函数切块、profile 路由、决议记录随 doc_version 归档 objectstore（键 `{doc_id}/{version}#decision`）、短节并入父节、F5 下游输入门限截断告警（M4-l）；overlap=0 + breadcrumb + 邻居回取口径。
- 放行项：结构识别第②③级（版面小模型 + LLM few-shot，需模型/云出口）。~~`ChunkDecision` 由 doc 级重设计为 node 级=一级契约变更~~ → **已闭合（M4-cg）**：R7 手续已走（用户批复「替换式」＋本 ADR 预告），node 级形状、规则档第①级判型与 D-2 三型档位执法落地，权威决策件＝[ADR-010](ADR-010-chunkdecision-node-level.md)。
