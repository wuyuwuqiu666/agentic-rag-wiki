# ADR 索引（架构决策记录，C3 历史层）

| # | 标题 | 状态 |
|---|---|---|
| [ADR-001](ADR-001-no-framework-checkpoint.md) | 断点续传不依赖编排框架 | 采纳 |
| [ADR-002](ADR-002-dual-write-order.md) | PG 事务先行→Milvus 后写 + 对账补偿 | 采纳 |
| [ADR-003](ADR-003-b0-total-gate.md) | B0 总闸：冻结写侧、保读侧、双人复位 | 采纳 |
| [ADR-004](ADR-004-local-embedding-rerank.md) | 本地 embedding/rerank 选型（Qwen3-0.6B / bge-reranker-v2-m3） | 采纳 |
| [ADR-005](ADR-005-single-machine-execution-tier.md) | 单机执行档 + 按端口定供给（embed/rerank 本地、LLM 云） | 采纳（待 v0.7.1 评审） |
| [ADR-006](ADR-006-b4-hardening-evaluation.md) | B4 转硬断评估：执行档维持 shadow 至企业档 | 采纳（评估结论） |
| [ADR-007](ADR-007-process-split-decision.md) | 分区进程级拆分决策点：单机档默认不拆 | 采纳 |
| [ADR-008](ADR-008-chunking-contextual-retrieval.md) | 切块策略契约与 Contextual Retrieval 默认关闭（原草稿误编 ADR-003，编号归位） | 采纳 |
| [ADR-009](ADR-009-parse-returns-parsedbody.md) | 解析产物改为 ParsedBody（正文+页态+OCR 账），扫描页识别落在解析边界 | 采纳 |
| [ADR-010](ADR-010-chunkdecision-node-level.md) | 决议记录由 doc 级单值判型改为 node 级节点段列表（`ChunkDecision{profile, decisions[], doc_token_count}` + `NodeDecision`） | 采纳（一级契约变更，破坏性迁移见 ADR「迁移与代价」） |
| [ADR-011](ADR-011-mq-lease-ack.md) | `MqPort` 改租约消费（`drain` 取得 / `ack` 确认前缀、其余退回队首），B 区编译把 B0 前置到取事件之前 | 采纳（端口形状新增，非 pydantic 冻结面） |
| [ADR-012](ADR-012-trip-ledger-startup-replay.md) | 触发台账从 append-only 审计账本启动回放（单一判据 `counts_as_hard_trip`）＝重启不再是双人复位的旁路；冻结期门面如实 `seed=skipped` 不带走读侧 | 采纳（零新存储/零契约变更/零新入口） |

新增/推翻决策须在此登记并同步 PRD 附录 B。
