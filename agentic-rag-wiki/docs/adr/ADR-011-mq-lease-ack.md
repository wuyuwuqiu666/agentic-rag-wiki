# ADR-011：`MqPort` 由"取走即消费"改为租约消费（`drain` 取得 / `ack` 确认），B 区编译把总闸前置到取事件之前

- 状态：采纳（2026-10-16，用户批复「继续完成 PRD 内容，如需要删除大量文件再申请，其余全部放行」；本条属全生命周期审计 #6 的收口刀 M4-cp）
- 关联：FR-WIKI-2（增量编译消费 canonical fan-out）、FR-WIKI-3（源删除→发布页标失效）、§6/B0 总闸"冻结全部写侧、保读侧"、ADR-003（B0 覆盖范围）、ADR-005（执行档队列=PG `SKIP LOCKED` 语义）、M4-z（`MqPort.depth` 入端口契约）、`src/platform/providers/ports.py`（`MqPort`）、`mq_pgqueue.py`、`mq_kafka.py`、`partitions/wiki/runtime/loop.py`、`gateway/composition.py`、`tests/contract/test_mq_lease_and_gate.py`、`tests/contract/test_store_mq_dual_backend.py`。

## 背景
`WikiLoop.run_once()` 的写法是"先 `drain` 最多 1000 条，再逐条写页"，而 `PgQueueMq.drain` 是**弹取**（`q[:n]` 出、`q[n:]` 留）。两处各自合理，合起来是静默丢事件：

1. **中途失败＝整批蒸发**。循环里任何 fail-loud（`CanonicalEvent.model_validate` 撞上破坏信封、`_compile` 读不出正文、下游 `IngestionPort` 抛错）都会带着**已 drain 未处理的那部分**一起退出：它们不在页上、不在清单里、也不在 `depth` 读数里。运维看积压=0，以为追平了。
2. **B 区回路根本没有闸**。A 区 `admit`/`withdraw`/`apply_changes`/`process_document` 五处都过 `ensure_write_allowed`，`run_once` 一处都不过。于是 B0 拉闸期点一次「跑一轮编译」＝把 fan-out **消费掉但不落任何写侧产物**——"冻结全部写侧"（§6）实际只剩索引那一半，冻结还附带制造了一次不可恢复的数据丢失。
3. **`depth` 的账实口径本就要求租约**。M4-z 把 `depth` 升为端口一等契约、E4 用它做"摄取积压"传感；但只要 `drain` 即消费，"正在处理中"这一段在指标上就是隐形的——卡住的那批既不计积压也没产出，正是传感最该看见的形态。

## 决策
1. **端口形状改为租约消费**：`MqPort` 增 `ack(topic: str, count: int) -> int`。`drain`=**取得**（条目进入在租状态，仍计积压），`ack`=确认在租条目的**前 `count` 条**已消费、其余**按原序退回队首**（返回退回条数）。`depth` 明确声明**含在租未确认部分**。
   - 前缀语义而非逐条 id：消费侧只需回答"跑到第几条了"，不必持有消息句柄，跨档无第二种口径。
   - 退回**队首**（不是队尾）：fan-out 是有序事件流，重投打乱次序＝`delete` 跑到 `upsert` 前面。
   - `count` 越界（负或大于在租数）＝`ValueError` fail-loud：静默夹紧会把"少确认"读成"都消费完了"，与 NFR-8 同一条红线。
2. **两档各自的原生映射，不造第三种语义**：
   - `pg_queue`（执行档）：`_leased[topic]` 记在租，对应生产 PG `SKIP LOCKED` 的行锁——取走≠提交，连接断了锁自动回到队首。
   - `kafka`（企业档）：消费者配置加 **`enable.auto.commit=false`**（自动提交会让 `ack` 这条缝形同虚设），`ack` 前缀逐条 `commit(message=…, asynchronous=False)`、其余 `seek(msg)` 回退读取位；未提交 offset 在进程/rebalance 后天然重投＝至少一次。`depth` 按已提交 offset 度量 lag，故在租部分天然计入。
3. **消费侧次序钉成不变式**：`WikiLoop` 注入 `GuardPort`（可选参数，缺省 `None` 保持既有单测构造可用），`run_once` 顺序固定为
   `ensure_write_allowed("wiki")` → `drain` → 逐条处理（`done += 1`）→ **`finally: ack(done)`**。
   两条各自承重：门在前＝冻结期**零 drain、零消费**；`finally`+前缀＝失败时已完成的不重做、没跑的一律退回。异常照旧上抛，不吞、不假装跑完。
4. **组合根把同一册总闸注进 B 区**（`WikiLoop(..., guard=guard)`），不给 B 区另立第二本触发账——另立＝只冻住 A 区，缺陷换层复现。
5. **分区标签 `partition="wiki"`**：审计里按分区定位处置作业，A 区账≠B 区账。

## 后果
- **正面**：`/api/wiki/compile` 在 B0 拉闸期返 503 且 fan-out 一条没少，复位后再点即补上推迟的那轮编译（HTTP 契约测试已锁）；中途失败时积压读数如实反映"还剩多少活儿"；A/B 两区在"什么算被冻结"上同一口径。
- **至少一次的代价（如实登记，不粉饰）**：
  1. **毒事件头阻**。永久坏掉的信封（如缺必填字段）停在队首＝每轮都在同一条上炸、其后的事件永远排不到（`test_poison_event_blocks_the_head_loudly_every_round` 把这一行为钉成了可见事实，而不是悄悄旁路）。处置＝修事件源；**死信队列/最大重试次数属企业档放行项⑯**，本环不伪造"自动旁路坏消息"的能力——自动跳过坏消息＝另一种形式的静默丢事件。
  2. **重投可能重放已成功的副作用**。B 区 `admit`/`withdraw` 落到 A 区是确定性块 id 的覆写（幂等，M4-r 口径），故 at-least-once 不产生重复页；换实现时若引入非幂等副作用，须由该实现自己保证。
  3. **吞吐**。Kafka 逐条同步 commit 是"正确性优先"的选择，批量提交/异步提交的性能数字**未测**，属放行项⑯（不谎称已优化）。
- **契约兼容性**：`MqPort` 是 `providers` 层的 `Protocol`（端口形状），不是 pydantic 冻结契约面（E3 `contract_compat` 快照的是 `_MODELS`），故 `ack` 属**新增端口方法**、不触发基线变更；实现方必须补 `ack`，否则不满足契约（`test_mqport_declares_lease_ack_primitive` 锁声明，两档实现各有测试）。
