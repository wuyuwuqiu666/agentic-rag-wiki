# ADR-005：单机执行档 + 按端口定供给档位

- 状态：采纳（待 PRD v0.7.1 整体评审确认）
- 决策一（缩量）：架构按企业档设计、执行按单机缩量跑。存量 5 万文档/≈1M 向量，G1=48h 承诺 + 26h 冲刺（据本机 P3 实测稳态 10.6 chunk/s，`../bench/p3-results.md`，76% 余量）；G2=1 千变更/日、P95≤15min 不变；≤1 QPS/≤5 并发/in-flight LLM 上限 8。分区制/六层/B0–B5/决议记录架构不缩水。R8（百万级）转企业档参考归档。
- 决策二（按端口定档，禁全局一刀切）：`EmbeddingPort`/`RerankPort` 主档=local（云=加速换插位）；`LlmPort` 主档=cloud（本地 3B GGUF 按需换载）。后果=密级语料默认不出机、G1 不依赖外部配额、R11 云依赖收窄至 LLM 出口一路。
- Milvus 不回落 pgvector：双实现对账是核心展品；MQ 降级为 PG `SKIP LOCKED` 队列（挂 MqPort 后）。
- 换插演练（M4）：embedding 双向断云续灌、LLM 双向断云换载、整机断网跑通，全程零脏数据。
