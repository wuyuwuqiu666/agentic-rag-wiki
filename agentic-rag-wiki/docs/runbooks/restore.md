# Runbook：备份与恢复（E6 / PRD §10 数据完整性、§15 E6）

范围口径=执行档（单机，ADR-005）。数字（RPO/RTO）保留为**脚本验收参数**，企业档容灾另计。
三件成对备份（F1 连带，缺一即恢复不可达）：**PG（canonical 清单 + 决议记录）+ Milvus/向量索引 + canonical objectstore（原文与切块）**。三者必须同一一致性点，否则对账必现背离债（→B5）。

## 备份（写侧）
- 频率：增量每 ≤1h 一次（RPO≤1h），全量每日一次；影子索引天然可回滚，不进备份主链。
- 单机档介质：本地目录 + **加密外置盘**（`workspace/backup/` 落清单，外置盘落实体）；企业档=对象存储版本化 + 跨区副本。
- 一致性点：以 PG 事务序列号（LSN）为锚，Milvus 记录同步水位、objectstore 记录清单快照 id——三者 LSN/水位/快照 id 成对写入 `manifest.json`（备份元数据，append-only）。
- 禁字面密钥入 manifest（T1）：加密密钥走环境变量/keychain。

## 恢复（读侧）
1. 停写侧入口（`GuardPort.ensure_write_allowed` 置冻结，或直接 B0 拉闸）——恢复期禁双写。
2. 按 `manifest.json` 锚点依次还原：PG → objectstore → Milvus（正序还原，避免索引悬空引用缺失原文）。
3. **对账差集**（复用 M1-b `reconcile()`）：PG 清单 ↔ 向量索引 ↔ objectstore 三方差集；差集非零按片重放，幂等键保证不重复入库（FR-ING / ADR-002）。
4. 冒烟：跑 `tests/smoke` 一条最小闭环 + 一次问答读路径，确认读侧服务正常再解冻。
5. RTO≤4h：超阈即升级人工，不强自动续。

## 恢复演练（季度一次，出报告）
- 频率：季度一次；脚本化（`workspace/drills/` 落每次演练记录：时间/锚点/三方差集数/耗时/结论）。
- 通过判据：三方差集=0、读路径冒烟绿、RTO 实测 ≤4h。任一不满足→列缺陷并排下次演练复验。
- M4 实机放行项：接真实 PG+Milvus 后本 runbook 的 `manifest`/`reconcile` 走真双存储口径（当前 stand-in=内存清单↔内存索引，见 AGENTS §8 诚实账）。

## 保留窗到期后的"物理销毁"尚未实现（审计 #12／#16，登记态·未闭）
`ObjectStorePort` 只有 `put`／`get`／`exists` 三方法（现档 `src/platform/providers/ports.py:75-80`）＝**执行档没有任何正文删除原语**。由此两条如实结论：
- **磁盘只增不减**：旧版本正文与切块/决议归档件写入 `workspace/canonical-draft/`（`composition.py:137`、`objectstore_fs.py:13`）后无人清；容量治理此刻是**部署侧的事**（外置盘/对象存储生命周期策略），不由本环自动执行，本档的备份链也刻意 append-only——**备份不可变与合规真销毁是两回事，勿把"不覆盖旧锚点"读成"永不删除"**。
- **墓碑保留窗到期＝"不可恢复"，不等于"已销毁"**：`ingest_retire.md` 里那两种不可恢复（过窗／`has_body=false`）在现档只有一种真成因＝**清单指针缺失或从未登记**，因为系统从不删正文。故读到 `has_body=false` 时应查指针与备份件，而不是推测"被清理了"。
- **对外口径红线**：合规若要求"到期即物理销毁并可举证"，本档**尚未具备该能力**，不得据 30 天窗口的文案声称已实现。
- **闭合需要什么**（为何不顺手修）：①端口加 `delete`＝跨 provider 契约变更（dev/FS/S3 三档必须同语义，且 `contract_compat` 快照的是 pydantic 模型、Protocol 需 ADR 留痕，仿 ADR-009）；②真删＝**不可逆动作**，按 T3 须先有 Git/备份基线（审计 #1 未裁决）；③"谁在何时销毁了什么"要一类贴切审计 kind＝**放行项㉑**；④保留窗与权限行的真口径落在真实 PG＋Milvus 双存储＝**放行项②**。登记于 `docs/audit/lifecycle-audit-2026-10-14.md` #12/#16。

## 回滚点
- 影子索引 / 别名切换：双别名保留上一版本 72h，一键切回旧别名（见 PRD §10）。
  **可达入口（M4-cm 起，本档为唯一处置页）**：重建＝`POST /api/ingest/reindex`（`confirm=true`）或 CLI `admin reindex --confirm`
  →`IngestPipeline.shadow_rebuild`（影子重建+原子切换，切换失败即时回滚）；事后人工整域退回旧版本＝
  `POST /api/ingest/alias/revert` 或 CLI `admin alias-revert --from-alias <新版本> --to-alias <保留旧版本> --confirm`
  →`IngestPipeline.revert_alias`（同名校验先于触库、store 抛错原样上抛不静默）。
  **两条都判 B0 总闸且判门在写侧之前**：冻结期以 503（CLI＝退出码 1）结束、零部分效果——先按 `breakers.md` 双人复位，再谈重建。
  读数解释：`applied=false` ∧ `rolled_back=true` 是设计内的失败形态（旧别名仍在服务），不是故障升级；
  `ProviderError`＝切换与回滚双失败、**别名可能悬空**，此为最高优先位，人工按本档第 3 条对账后再决定重建或切回，不得连点。
  真实 collection 别名随 PG+Milvus 双存储落地（AGENTS §8 放行项②）；执行档单 store 不核验 72h 保留窗、也不给前端按钮。
- tombstone 恢复：B1 隔离区软删 30 天保留期内可经恢复作业回转（双人确认放行）；逐步操作与墓碑账字段读法＝`ingest_retire.md`（唯一源）。
- 备份不可变：`workspace/backup/` 清单 append-only，恢复只读旧锚点，绝不覆盖新锚点。
