# Runbook：运维读回与复位（Admin 五方法 × HTTP/CLI 两面 / M4-x、M4-ax、M4-ay、M4-ae、M4-ci、M4-cj）

范围口径=**运维面"看什么数、数怎么读、什么时候该动手"**。熔断阈值与判停语义见 `breakers.md`；容量降档的答案形状见本页第 3 节。

## 1. Admin 面入口矩阵（不对称是刻意的，先说明）
> 本表按"Admin 方法 × 两面"排，带**参数级**细节（哪个端点缺 window 参数、哪条命令须 `--confirm`），只覆盖运维读回与写侧修复相关入口；
> **全量可达入口的 1:1 清单＝`entry_matrix.md`**（M4-cl 起 `runbook_check` 逐条拿代码注册位比对，多一条少一条都红）。
> 两表分工：入口齐不齐、归哪一页处置＝看 `entry_matrix.md`；命令参数与端点缺口＝看本表。新增入口须同进 `entry_matrix.md`。

| Admin 方法 | CLI | HTTP | 副作用 |
|---|---|---|---|
| `replay_audit` | `admin audit-replay [--kind K]… [--since-ts F]` | `GET /api/admin/audit?kind=&limit=` | 只读 |
| `pool_budget` | `admin pool` | `GET /api/admin/pool` | 只读 |
| `funnel_stats` | `admin funnel [--window-seconds F]` | `GET /api/admin/funnel`（**无 window 参数**，取全量） | 只读 |
| `degrade_plan` | `admin degrade-plan --total N --bad N [--slo-target 0.995]` | **无端点** | 纯计算，无副作用 |
| `reset_breaker` | `admin reset --breaker B? --operator X --approver Y --reason Z` | **无端点** | **写侧动作** |
| （非 Admin：A 区 B5 对账）`IngestPipeline.reconcile` | `admin reconcile` | `GET /api/ingest/reconcile` | 只读，**且只读不传感** |
| （非 Admin：A 区缺失块补偿）`IngestPipeline.compensate` | `admin compensate --confirm` | `POST /api/ingest/compensate`（`confirm: true` 必填） | **写侧动作**（重放入库），先判 B0 总闸、再判 B5，一次动作只传感一次 |

**B5 对账为什么单列一行**：它的账本在 A 区 `CanonicalInventory`（源块册＋回写块册），不经 `Admin` façade，端面 1:1 委派同一个 `reconcile(sense=False)`——两面同一本账同一口径，不在命令面另算一套读数（M4-ci）。读数与处置路径（`missing`/`extra`/`writeback` 怎么读、缺权威页块为何不经补偿）唯一源＝`wiki_review.md` 的"对账里的权威块"节。
**`sense=False`＝只读不传感**是运维安全而非洁癖：读回入口会被反复刷新，而 B5 硬断档下每次传感越阈都记一条未复位触发、连续达阈即升 B0 冻结全部写侧——"看一眼账"不得变成"自己把系统拉闸"。传感仍留在写侧动作（补偿/恢复）里；同一条背离在写侧传感时照记观察（测试锁＝`tests/contract/test_reconcile_entry.py` 第二例）。
**读与写分门（M4-cj）**：`GET` 只给数不动账，改索引的是 `POST /api/ingest/compensate`——把补偿做成 GET 的副作用＝"看一眼账"顺手把库改了。补偿**故意没有前端按钮**（同上一条"复位只在 CLI"口径：批量写侧动作不给一次点击的可达性），回执与 `report` 路径读法、以及"判门在动手之前→硬断档下 503 且零部分效果"的处置顺序唯一源＝`wiki_review.md`。

**B0 冻结期点补偿＝503，不再回"什么都没做"的 200（审计 #14，已收口：M4-cv 2026-10-19）**：
- **收口前的形态（时点标注，供比对历史回执）**：`compensate()` 内**唯一的门是 B5**（`guard.check("reconcile_missing_chunks", …)`，前置在重放之前）；B0 总闸（`ensure_write_allowed`）住在 `process_document` 里面，所以**这一批没有正文可重放时重放循环一次都不执行＝B0 从不被问**。后果不是改坏索引（无块可补就没有写侧动作），而是**回执形状**：冻结期它照样把对账报告写进 `workspace/reconcile/` 并回一个成功形状的 200＋`backfilled:0`，容易被读成"账实相符、不必处理"，而真相是"此刻写侧被冻住，本轮没有做任何补齐动作"。
- **现档（M4-cv）**：函数首行先问 B0（`ensure_write_allowed("ingest")`），再按债问 B5——**整批在第一步被拒**：503＋点名"写侧已拉闸（B0）"、零重放、零报告、索引不动；CLI 同一道门（退出码 1）；读侧 `GET /api/ingest/reconcile` 不受影响（总闸冻写侧、保读侧）。
- **这是行为变更，用户已放行**：它把**已发布端点在冻结期的语义从 200 改成 503**，不是补漏；本条按用户放行「#14 补偿补 B0 前置门」落刀，M4-cj 的真实 HTTP 判据随刀重跑（读数记在 `docs/audit/lifecycle-audit-2026-10-14.md` #14 行）。
- **读法（M4-cv 之后）**：`missing=0` 的 0 现在可信＝"账上确实没债"；判"此刻是否被冻"仍看审计账本里该闸**有 `breaker_trip` 而无配对 `breaker_reset`**（`GET /api/admin/audit?kind=breaker_trip,breaker_reset`，读法见 §5），或按 `breakers.md` 的 B0 条目复位后再跑一次补偿。**注意 `/api/health` 回答不了这个问题**：它的 `seed` 字段只在**启动那一刻**被冻过才留 `skipped:…`，运行期新触发的冻结不会回填 health。

**后两条为什么不上 HTTP**：复位是解除 B0 冻结的写侧动作，执行档鉴权面尚未落地（E9 治理放行档）——把一个无鉴权、有副作用的动作裸暴露成 HTTP 端点，等于让任何能连到端口的人参与复位；`degrade-plan` 无副作用但须真实窗口计数才有意义，本机给不出该计数。因此**"双人复位"的唯一可达入口＝上面那条 CLI 命令**，前端不放复位按钮。执行档"HTTP 面故意不暴露复位"这条口径自本页起为权威表述（`breakers.md` 回指此处）。

## 2. 三本独立账怎么读（`/api/admin/pool`）
读数键：`inflight{qa,wiki_compile,evolve}` / `own{}` / `borrowed{}` / `total` / `hard` / `peak` / `at_peak`。
- 出厂额度 **qa 5 / wiki_compile 2 / evolve 1**，合计 `hard=8`＝B2 hard＝`Settings.inflight_llm_limit`（三处等值由机检锁死，防"8"漂移）；`peak=6`＝`b2.yaml` 的 soft 阈（**不另立阈值表**）。
- `borrowed` 非零＝临时借出窗口，按规则④随借方调用跑完**自动归还，无须人工调额**；`at_peak=true`＝离线池此刻再借将被拒（问答优先，防饿死在线流量）。
- 空读数 `{}` ＝账本未装配，**不假造数据**。此时不代表"零在飞"，只代表没有读数来源。
- 额度切分（5/2/1）系设计初值，**未经 shadow 定标**（A2 欠账在册），别把它读成已验证的容量结论。

## 3. 降档：两条不同信号，不要混读
- **读侧 in-flight 阶梯（执行档有执法点）**：问答链入口取一次档，档由 `PoolLedger` 在飞总数对 B2 `soft/hard` 判出——`<soft`＝档 0 四层全开；`[soft,hard)`＝档 1 弃④（`max_hops=1`，停 agent 多跳）；`>=hard`＝档 2 弃④③（**不发起 LLM 生成**，只给②层已编译 wiki 权威页作次好答案，无页则如实拒答）。用户可见前缀 `【高负载降级】`；降级件**不入语义缓存**；降级事实经既有 `breaker_trip` 记 `action=alarm`（不新增 E3 冻结枚举）。立场：阶梯**只减功能、不加失败**。
- **错误预算燃尽判级（`admin degrade-plan`）**：输入是**窗口计数**（`--total/--bad`，外部输入构造期校验），输出 `level`(0–3)/`exhausted`/`reason`；燃尽阈 1×/2×/4× 逐级加深。诚实边界：**第 3 级"全平台静默"在执行档无执法点**，逐日 SLO 作业、实时可用性喂数、read 路径据 plan 自动降档＝运行时放行档（**放行项⑬，需真实流量**）。本面提供的是可调用、可审计、可单测的**决策计算核心**，不伪造调度器、不谎称已自动降档。
- 运维动作：读到 `at_peak=true` 或答案带降级前缀 → 短时峰值属正常降级**无需复位**；持续燃烧则走 config 扩并发或限流入口（**不是改代码**，也不是改 `b2.yaml`）。

## 4. 消费漏斗怎么读（`/api/admin/funnel`）
读数键：`window_seconds` / `effective` / `refused` / `counts{cache,wiki,single_hop,multi_hop}` / `rates{}` / `targets{cache:0.30,wiki:0.25}` / `evaluated` / `unmet`。
- 分子＝层命中，分母＝**有效请求**；**拒答不入分母**、另记 `refused`（否则拒答越多命中率越"好看"）。
- `evaluated=false` ＝**无样本不判定**，`unmet` 为空不代表达标。执行档不承诺命中率数字（v0.7 撤下该指标、继承爬坡期条款）；真实爬坡结论须企业档启用当日喂真实流量，**不以合成流量或零样本冒充**。
- 想按滚动窗看：CLI `--window-seconds`，HTTP 面目前只给全量（在册差异，非缺陷）。

## 5. 审计回放与复位
- 回放：`admin audit-replay --kind breaker_trip --kind wiki_publish [--since-ts <epoch>]`（CLI 支持时间下界）；HTTP `GET /api/admin/audit?kind=a,b&limit=N`——`limit` 夹在 1..500、取**尾部**最新 N 条。只读回放，append-only 不受影响。
- 复位：`admin reset --breaker B1 --operator 张 --approver 李 --reason "根因已修+复验记录链接"`。入口结构化强制**两签署人相异且非空、理由必填**（`ValueError` 即拒），委派 `PolicyGate.reset_breaker` 落 `breaker_reset` 审计（`by=operator+approver` 留双签痕）。真·跨人授权审批流＝E9 治理放行档，本面只做入口强制，不声称已完成审批链。
- **重启不解冻（M4-cs／ADR-012）**：上一步的 `breaker_reset` 之所以是"唯一"复位入口，是因为启动时组合根会 `guard.restore_from_audit()` 从这本 append-only 账本**重放未复位硬断**（判据单点在 `trip_ledger.counts_as_hard_trip`：影子观察与 B0 升级读数都不算）。运维读法：① 拉闸后重启进程，写侧仍 503 且 `GET /api/admin/audit?kind=breaker_trip` 尾条的 `extra.value` **不变**（没清零、也没被灌大）；② 复位后重启，写侧恢复且 `breaker_reset` 仍在账；③ 若启动即 503，先按 `breakers.md` B0 处置而不是反复重启——反复重启不产生任何复位留痕，本身就是异常信号。
- 复盘锚点：发布署名在 `wiki_publish` 事件的 `detail` 里（`审阅人=…`，随回写块数与 ACL 交集同条）；降级事实在 `breaker_trip` 的 `extra.degrade_level`。9 值 kind 的覆盖不变式由机检兜（`harness` 侧），`param_change` 在执行档不触发属在册声明而非遗漏。
