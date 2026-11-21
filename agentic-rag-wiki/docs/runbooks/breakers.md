# Runbook：熔断闸操作（B0 / B1 / B2 / B3 / B4 / B5）

复位一律双人、填理由，`AuditSink` 落 `workspace/audit/`（append-only）。规则阈值改动须附 ADR 引用（`param_class_check` 拦安全参数自动改）。
复位命令的唯一可达入口＝CLI `python -m src.platform.gateway.cli admin reset --breaker … --operator … --approver … --reason …`（**在仓库根目录下执行**；执行档没有 `agentic-rag` 这个可执行程序——`pyproject` 无 `[project.scripts]`、亦无 build-system，写短名即 command not found，审计 #9／M4-ct 起本页与 argparse `usage` 同一口径，机检见 `tests/smoke/test_cli_invocations_documented.py`）；**执行档 HTTP 面故意不暴露复位**（写侧副作用动作，鉴权面属 E9 放行档，裸暴露即越权可点）——口径权威表述与两面入口矩阵见 `admin_observability.md`。
**复位必须有复位对象（M4-ct，审计 #9 顺带查出）**：一条未复位的**真实硬断**都不存在时，`admin reset` 直接拒绝（非零退出＋`ValueError` 说明"复位无对象"）且**审计里零条目**。旧行为是返回成功并落一条 `breaker_reset`＝把"没触发过"记成"已复位"，而重启回放（M4-cs）会把这条假账原样读回台账。据此，**B2（软告警）、B4（shadow）与 dev 默认观察档下的 B1/B5 都没有可复位的对象**——它们从不进触发台账；要给它们"复位"，先确认该闸真的以硬档触发过（`python -m src.platform.gateway.cli admin audit-replay --kind breaker_trip` 查）。测试锁＝`tests/contract/test_admin_surface.py` 三条拒绝用例。

## B0 总闸
- 触发：连续未复位触发数 ≥3（`b0.yaml`），或失效/人工三类触发。
- 现象：写侧全冻结、读侧保留。
- **冻结＝推迟，不消耗待办（M4-cp／ADR-011）**：判门一律前置在任何"取用队列事件"的动作之前，所以拉闸期 `POST /api/wiki/compile` 返 503 且**零 drain 零 ack**——fan-out 积压留在队列里，复位后再点一次即补齐。若某次冻结把待办吃空而账面显示积压归零，那是缺陷不是设计（判据：`tests/contract/test_mq_lease_and_gate.py`）。
- **`depth` 读数口径＝未读＋在租（M4-cp 修订）**：被某一轮取走但尚未确认的事件**仍计入积压**（"取走即扣减"的旧读法会让正在处理/中途失败的那段在指标里隐形）。因此拉闸后看到"积压不为 0"是**预期状态**，不是"处置失败"；复位后跑一轮编译才归零。B 区各现象的处置步骤归 `wiki_review.md`（本页不重述）。
- **重启不是复位（M4-cs／ADR-012）**：未复位的真实硬断在**每次启动时从 append-only 审计账本如实重放**（`guard.restore_from_audit()`，只读账本、不写审计、不改阈值）。所以"把进程停一下再起"解冻不了写侧，审计里也不会凭空多出一条 `breaker_reset`——复位只有下面第 ③ 步那一条路。三条配套口径：
  - **单口径**：什么算"真实硬断"由 `trip_ledger.counts_as_hard_trip()` 一处定（`shadow is False ∧ action=="block"`）。影子档观察（开发默认 B1/B5 只记不拦）**不跨重启执法**；B0 升级读数（每次被挡的写各追加一条）**不回灌触发计数**——否则冻得越久重启后"欠的债"越多，执法被自己的观测喂大。
  - **读不懂即拒起**：账本行损坏则容器不启动（fail-loud），绝不"读不到就当没有未复位硬断"。修账本本身属于放行动作，需人工确认。
  - **冻结期起门面是合法的**：`/api/health` 仍 200 并如实报 `seed: "skipped:…"`（演示语料＝一次写侧动作，被总闸挡下就跳过，不炸启动、不假装成功）。看到 `skipped` 不是缺陷，看到写侧 503 才是拉闸的真实状态。
- 处置：① 查 `escalated_from` 定位源闸；② 修根因；③ 双人复位（唯一可达入口＝CLI `admin reset`，见 `admin_observability.md` 第 5 节）并写 `breaker_reset` 审计（禁静默）。**升级本刀后首次启动若直接返 503**，是历史账本里确有未复位硬断（老环境欠的债不因升级消失），按 ①–③ 走即可；确认无需追认时用 CLI 复位留痕，勿改用删账本文件的办法。

## B1 批量删除（硬断）
- 阈值：软 1% / 硬 10%（`b1.yaml`）。
- 处置：进隔离区，软删 + tombstone，30 天保留期内可经恢复作业回滚；放行须双人确认。
- 落地（M4-q，离线代码路径）：删除经 `IngestPipeline.apply_changes` 落活跃索引移除 + `DeleteLedger` 记可恢复墓碑（正文留 objectstore，store_key 指针）。恢复执行语义（`restore_deletes` 重放幂等、过窗/正文真丢计入不可恢复、`DeleteLedger` 内存态＝stand-in）**单一源＝`ingest_retire.md`**。真 30 天保留与跨进程持久=PG 放行项②。
- 可达入口（M4-ce 1a）：下架/改域 → 墓碑台账 → 回滚 三条端点、回执字段读法与前端位置，**单一源＝`ingest_retire.md`**（本页不重述清单，防两处漂移）。本闸只管其中一条判停语义：**占比按批算**。
- **操作前必读的三条边界**：①**硬断档下单文档下架必被本闸判停**——占比按"本批删除块数/当日存量"计，一条文档＝ratio 1.0，`Settings.shadow_breakers` 清空（转全硬断）后该入口即 503 且**零部分效果**（索引未动、墓碑未记，`test_hard_b1_blocks_single_document_takedown` 钉住）；dev 默认 `"B1,B5"` 观察档故现可用。**"人工发起的单文档下架是否豁免批量删除闸"属阈值语义决策（§7 禁改区，改须 ADR＝实机放行项㉑），本刀不擅改 `b1.yaml`**。②**下架后 wiki 权威块的停答有一轮 compile 的异步窗**——`_invalidate` 只在 `WikiLoop.run_once()` 消费 fan-out 时执行，故下架已发布页的源后需 `POST /api/wiki/compile` 才见撤块（回执会如实说明）。③**`actor` 目前仅回显、不落审计账**——`AuditEvent.kind` 是 E3 冻结 Literal，无贴切成员表达"谁下架了什么"，硬套 `breaker_trip`/`acl_denied` 会把运维动作混进安全语义；补专枚 kind 须人工 ADR + `contract_compat --update`（同㉑）。
- 已兑现（M4-cd）：`acl_change` 效应②（源块 ACL 在**两路索引＋清单**重写）与效应④（受影响权威页随 fan-out 在下一轮编译标脏撤块，重合成的块须**重新署名发布**才回索引）；回执以 `effects_done`/`effects_pending` 分列，效应①PG 权限行仍＝放行项②。**受众收紧分两段且写进回执**：源块即刻换域，但该页已发布的权威块在下一轮编译前**仍按旧受众可读**（测试锁＝`tests/contract/test_writeback_lifecycle.py` 第⑤条）；关窗动作＝改完域立刻跑一轮编译。另"已删源的草稿可经人审复活"已封：发布前有**依据存活门**（经 `IngestionPort.is_indexed` 读 A 区唯一那本清单，不在册即 409「依据已失效」并指路 §10 恢复），恢复该源后同一草稿照常可发布。

## B2 容量 / in-flight（软告警，不阻断）
- 阈值：软 in-flight 6 / 硬 8（`b2.yaml`）；越 soft 报 `alarm`，绝不 `block`（自愈型，不计 B0 台账）。
- 现象：并发或 token/in-flight 预算打满，返回"次好答案"而非报错。
- 处置：① 观察是否短时峰值（短时=正常降级，无需复位）；② 持续燃烧→扩并发预算或限流入口（走 config，非改代码）；三本独立账读数与 in-flight 降档判读见 `admin_observability.md` 第 2–3 节；③ 长期容量不足→升供给档位（企业档）。

## B3 模型供给（硬断，含 embedding 出口）
- 触发：provider 失败率越阈，或本地稳态吞吐跌幅 >30%（`b3.yaml`/`b3_throughput.yaml`）。
- 处置：半开探测→暂停-续传（消耗时间预算非数据正确性）；embedding 断云→本地续灌；LLM 断云→按域换载本地 3B。演练见 M4。

## B4 注入与外泄（shadow 起步）
- 阈值：隔离占比 `injection_score`（`b4.yaml`，level=shadow=只记不拦）。
- 现象：摄取命中 DLP/注入模式，块进隔离区并审计 `quarantine`，但不阻断正常摄取。
- 出口（M4-p）：答案出口亦过同一 `service.dlp` 口径——命中即脱敏后呈现（shadow=不阻断会话）、发 `injection_score`→B4、审计 `quarantine`、脱敏件不入语义缓存；拒断（整体拦）留 B4 转硬断档启用。
- 处置：① 查隔离样例判真伪阳；② 转硬断须满足 ADR-006 三判据（真实注入分布 + 可接受假阳率 + 双人复核路径就绪），执行档结论=维持 shadow 至企业档；③ 开关纯配置（`b4.yaml` level + `shadow_breakers`），可逆。

## B5 数据完整性（硬断）
- 指标：PG↔Milvus 背离债（补偿队列水位滞后秒数，`b5.yaml` 软 60s/硬 300s）；执行档另有一条同族可离线跑的账＝`b5_missing.yaml`（`reconcile_missing_chunks`，软 1/硬 100）。
- 处置：暂停别名切换；跑对账差集；差集非零按片重放（幂等键保证不重复入库）。执行档的可达入口＝`POST /api/ingest/compensate`（`confirm: true`）或 CLI `python -m src.platform.gateway.cli admin compensate --confirm`（M4-cj，读法与处置顺序见 `wiki_review.md`）。
- **硬断档下的次序悖论（如实记）**：缺块 ≥100 时判门在动手之前，补偿整批 503 且零部分效果——正是最需要修的那一刻门最紧。阈值属禁改区不擅动；处置＝先按本页把债降到阈下（或人工分批核对）再跑补偿。只读入口不受此门影响，仍可反复看账。
