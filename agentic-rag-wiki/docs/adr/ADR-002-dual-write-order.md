# ADR-002：双存储写序 = PG 事务先行 → Milvus 后写 + 对账补偿

- 状态：采纳
- 决策：chunk 元数据先落 PG 事务，成功后写 Milvus 向量；Milvus 写失败不回滚 PG，入对账补偿队列，由 B5 看管背离债（补偿队列深度=背离指标）。
- 后果：读侧走旧别名（影子索引）无感；`harness/breaker_rules/b5.yaml` 阈值挂在此处；批量删除经 B1 隔离区放行、软删 + tombstone 30 天可恢复。
