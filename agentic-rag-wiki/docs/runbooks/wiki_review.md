# Runbook：B 区权威页审核台（FR-WIKI-1/2/3/5 / M4-cb、M4-cc、M4-cd、M4-ci、M4-cp）

范围口径=**人审台的操作步骤、状态机、撤块账与编译轮次的消费语义**。下架侧的源失效动作见 `ingest_retire.md`；拉闸动作本身的判定与复位见 `breakers.md` B0（"冻结期点编译会发生什么"归本页，不双写操作步骤）。

## 入口清单
| 动作 | HTTP | 前端（`WikiReviewView.tsx`） | CLI |
|---|---|---|---|
| 跑一轮编译（取 fan-out→候选→草稿，**租约消费**） | `POST /api/wiki/compile` → `{"new_drafts": n}`；**B0 拉闸期 503 且一条事件都不取用**（见下节） | 按钮「跑一轮编译」 | 无 |
| 页清单（待审/已发布/失效） | `GET /api/wiki/pages?role=&acl=&status=` | 左栏「权威页审核台」，按"接下来该做什么"排：待人审 → 草稿 → 源已变更，已发布/失效沉底 | 无 |
| 人审动作 | `POST /api/wiki/review {page_id, action, reviewer, role, acl}` → `{page, indexed}` | 三键＝「提交人审」/「署名通过并发布」/「打回」，`action` 对应 `submit`/`approve`/`reject` | 无 |

界面明写着这条门的落点：「草稿不可直接发布、空署名不可发布，这条门在后端状态机执法，界面无法绕过」——前端只是指路，**执法点永远在后端**。

页视图字段（`WikiPageView`）：`page_id/domain/title/status/acl_tags/source_doc_ids/reviewer/triage_reason/version/sections`。**`triage_reason` 是判据**——它解释这页凭什么入选或被筛。

## 状态机（唯一执法点 `wiki/service/review.py`，门面不判也不改判）
合法迁移只有这 8 条，不在表内＝`ReviewError`→**409**：

```
draft → in_review → published
  ↑        ↓（打回）    ↓（源大改/命中走低）    ↓（源删除）
  └── in_review    stale → in_review / draft   retracted → draft
```

- **草稿不得直接发布**：`draft → published` 不在表内，这是代码不变式而非文档自觉（P2）。
- **署名门**：`approve` 时 `reviewer` 去空白后为空＝409「发布须署名审阅人」。
- **依据存活门（M4-cd，FR-WIKI-3）**：页的每条 `source_doc_ids` 必须仍在 A 区清单在册，否则 409「依据已失效：源 […] 已删除/下架」且**零副作用**（页停在 `in_review`，索引里不会多出块）。存在理由：`_invalidate` 只够得着 `published` 页，源删除时**未发布的草稿**不会被标脏——没有这道门，"署名通过"就能把依据已死的块重新当权威供答案。处置＝按 §10 恢复该源（`POST /api/ingest/restore`）或等新版本入库并重编译，再送审发布；恢复后**同一草稿**即可发布，不要求重编译。判据取自 `IngestionPort.is_indexed`（A 区唯一那本清单），B 区不自立"哪些源已删"的第二本账（两本账迟早漂移）。
- 门是**预检在前**（先判依据存活→再判审阅迁移→再写索引、最后改状态）。存在理由：写侧若失败（B0 拉闸 / F2 空 ACL / NFR-8 越界），页不能停在 `published`——否则出现"页声称已权威、A 区里却没有"的账实背离（同 B5 家族）。

## 发布＝B→A 回写，全链无旁路
`approve` 走 `WikiLoop.publish` → `IngestionPort.admit`：**F2 入口硬校验**回写块 ACL 已由 `compile._intersect_acl` 合成为全部源块交集，空 `acl_tags` 直接 fail-loud（禁索引成公开块，封跨源页面泄露通道）；B0 写侧总闸同样覆盖此道（人审发布=写索引，拉闸期 **503**，不得经回写绕过）。发布成功后按其 ACL 域清一次语义缓存——发布承诺"该域引用即以 W 优先"，留着旧缓存就是等 TTL 才兑现。回执 `indexed` = 写入 A 区的权威块数。

**不得回退的不变式：索引里的 wiki 权威块 ⇔ 该页 `status=="published"`**。未复审的依据不得以 W 优先供答案。

## 冻结期跑编译＝推迟，不是丢失（M4-cp／ADR-011）
`run_once` 的次序是一条不变式，不是实现细节：

```
ensure_write_allowed("wiki")  →  drain（取得入租）  →  逐条处理 done+=1  →  finally: ack(done)
```

- **B0 拉闸期点「跑一轮编译」＝503 且一条事件都不取用**（判门在 `drain` 之前）。看到 503 的正确读法是"这批活儿被推迟了"，fan-out 积压仍在队列里可见；**复位后再点一次即补齐**，不需要（也无处）找回"被吃掉的那批"。判据实测：冻结前后 `depth` 3→3、零新草稿；复位后再点→`new_drafts: 3`、`depth` 0、草稿出现。
- **`depth` 读数口径＝未读＋在租**（M4-cp 修订，此前"取走即扣减"）。因此"积压不为 0"有两种含义：还在排队的，以及**已被某一轮取走但尚未确认**的（后者通常意味着那轮正在跑、或中途失败留下的待重投）。运维面读法见 `breakers.md`。
- **中途失败只重投未确认的部分**：`ack` 确认的是**已完成前缀**，其余**按原序退回队首**（不是队尾——fan-out 是有序流，重投打乱次序会让 `delete` 排到 `upsert` 前面）。异常照旧上抛，不吞。
- **队首毒事件＝每轮在同一条上响亮失败并阻塞其后**：这是**至少一次**语义的固有代价，本环**故意不自动跳过**（自动旁路坏消息＝另一种静默丢事件）。处置＝修事件源（或按 §10 重新入库该文档）；死信队列／最大重试次数属企业档**放行项⑯**，尚未启动。
- **越界确认会直接报错**：`ack(count)` 为负或大于在租条数＝`ValueError`（消费侧计数错乱必须响，不静默夹紧）。

## 发布成功而审计缺账的窗口（审计 #13，登记态·未闭）
`publish` 的次序＝零副作用判门（依据存活门→人审门）→ `admit` 写 A 区 → 改本页 `published`＋审阅人 → `registry.put` 建倒排 → **最后**才落 `wiki_publish` 审计。前面那一段顺序是**刻意**的（先有索引里的权威块，再声称页已发布，防"页说自己是权威而 A 区里没有"），残余的洞在**审计殿后**：审计件写失败时 `_emit` 不吞异常（fail-loud，调用方照样收到报错），可此刻块已入两路索引、页已 `published`＝**效果已发生、追责任一条账都没有**。

- **读法**：一次发布报"失败"时，**不得据审计缺条目判定"没发布成功"**。核对以两处为准＝`GET /api/wiki/pages` 的该页 `status`、`GET /api/ingest/reconcile` 的 `writeback`（回写块在册＝`admit` 已生效）。
- **处置**：页确已 `published` 而账上无 `wiki_publish`＝按"已发生的效果"补人工留痕（工单/变更记录），然后**不要**重放发布——同 page_id 重编译会顶替旧页（`PageRegistry.put` 语义），把已发布的页再走一遍是改账不是补账。撤发布再重发布须重新署名，属人审动作。
- **为什么不顺手修**：闭合＝把一次发布拆成 `intent`／`outcome` **两条账**（同 kind、`extra["stage"]` 区分＝零 E3 变更），代价是审计密度翻倍且"有 intent 无 outcome"这种半态要新判据（与 M4-cs 的"真实硬断判据"同族，判据一多就会分叉）。属用户点头项，登记于 `docs/audit/lifecycle-audit-2026-10-14.md` #13。

## 常见现象与处置
- **入库了但没有草稿**＝未通过 triage 入选，**不是失败**。入选门（`wiki/service/triage`）只放制度/规范/runbook/圈定/长文兜底作候选，纪要类不沉淀。想看判据读 `triage_reason`。
- **404「页不可读或无权限」**：与 `/api/source` 同一口径，**不区分"不存在"与"越权"**，免得被用来探测他域页的存在性。先按 `scope_409.md` 确认调用者的可见域。
- **409「该页缺编译时的源事件信封」**：dev 档的页态是进程内态，重启即失。处置＝重新 `POST /api/wiki/compile` 生成草稿再审发布，不要试图绕过。
- **源变了/源删了，权威块还在供答案**：撤块只在 `WikiLoop.run_once()` 消费 fan-out 时执行（`_invalidate` 按 `compile.build_writeback(page)` 推出的回写块 id 调 `withdraw`，并留 `AuditEvent(kind="wiki_invalidate", …)`）。处置＝跑一轮编译。审计读回：`GET /api/admin/audit?kind=wiki_publish,wiki_invalidate`（见 `admin_observability.md`）。
- **改了源的可见域，旧受众还看得到那张页**：源块即刻换域（检索与 `/api/source` 都读不到），但**已发布的权威块**带着发布时的旧 ACL，要等下一轮 `POST /api/wiki/compile` 才标脏撤块——这段窗是设计态，回执 `hint` 已如实写明（测试锁＝`tests/contract/test_writeback_lifecycle.py` 第⑤条）。处置＝跑一轮编译；撤下后重合成的块**须重新署名发布**才回索引＝受众变更由人签字，系统不自动复权。
- **重编译后页停在 `stale`/`retracted`**：这是设计态不是卡住——`stale → in_review` 复审后才重新入索引；`retracted → draft` 表示依据已失效，需重写。

## 对账里的权威块（B5 双册账，M4-cd）
`CanonicalInventory` 记两册：**源块册**（按 `(doc_id,version)`）与**回写块册**（`chunk_id → page_id`，发布时 `admit` 入账、撤块时 `withdraw` 出账）。据此 `reconcile()` 的四条读数：
- `extra`＝**真孤儿**（索引有、两册清单皆无）；发布过的权威块不再计入 `extra`（此前每发布一页就虚报一批孤儿，真背离被恒定噪声淹没）。
- `writeback`＝账里属权威页的块数；`missing`＝背离债合计，`compensate()` 再拆成 `missing_source`（可按清单指针重拉正文重放）与 `missing_writeback`。
- **补偿不臆造页**：`missing_writeback` 那份 `backfilled` 恒不含它——权威块正文只活在 B 区的页，A 区按 canonical 正文重放只会造出源块。处置路径回 B 区＝重编译→人审→重发布。
- 报告落 `workspace/reconcile/reconcile-*.md`，其中"缺失权威页块"单列一节并按页归组。
- **怎么看到这四个数**（M4-ci）：`GET /api/ingest/reconcile` 或 CLI `python -m src.platform.gateway.cli admin reconcile`（仓库根目录下执行；短名 `agentic-rag` 不可执行，口径见 `breakers.md` 首段）——两者 1:1 委派同一个
  `reconcile(sense=False)`，同一本账同一口径，命令面不另算一套。读回**只读不传感**：刷新页面不记 B5 观察，
  因而不会把"看一眼账"累积成"自己把系统拉闸"（硬断档下每次传感越阈都记一条未复位触发，连续达阈升 B0 冻结全部写侧）。
  背离债的传感仍留在写侧动作收尾（如恢复后自跑对账）与逐窗采样作业（真实调度＝放行档）。入口矩阵见 `admin_observability.md`。
- **怎么补（M4-cj）**：`POST /api/ingest/compensate`（body 只有 `actor` + `confirm: true`）或 CLI `python -m src.platform.gateway.cli admin compensate --confirm`，
  两者 1:1 委派同一个 `IngestPipeline.compensate()`。**没有"补哪几条"的参数是刻意的**：口径＝账上缺哪块补哪块，
  让人来挑就变成人肉臆造；正文真拉不回来的（无指针/store_key 不存在）不造内容，如实留在回执 `missing_after` 里。
  回执读法：`missing`＝本轮开始时的背离债合计，`missing_source`＝可重放那份，`backfilled`＝实际重放的**文档数**
  （整 doc 重放、同 chunk_id 幂等，故一块缺也补该块所在的整篇），`missing_after`＝补完仍欠的那份，`report`＝报告落盘路径。
  **判门在动手之前（两道门，M4-cj＋M4-cv）**：先问 B0 总闸——冻结期一律 503（`写侧已拉闸（B0）`）、零重放、
  零报告、索引一字未动，**与这一批有没有债无关**（旧实现在"没有正文可重放"时照样回 200＋`backfilled:0`，见
  `admin_observability.md` 的审计 #14 节）；再问 B5 单类闸：`missing` 达硬阈（`b5_missing.yaml`＝100）时同样整批
  503 且零部分效果，不会出现"补了一半没账"的态；此时先按 `breakers.md` 的 B5 处置页把背离降到阈下，再回来跑补偿。

## 回滚点
- 打回（`reject`）＝`in_review → draft`，零副作用，是发布前唯一的反悔位。
- 已发布页要撤回＝让它的**源失效**，两条路径都可达（M4-ci 后表单齐）：
  ① 下架（`POST /api/ingest/change kind=delete`／左栏「下架与恢复」）→ 一轮编译 → 页转 `retracted` 并撤块；
  ② 改可见域（`kind=acl_change`／左栏「改可见域」）→ 一轮编译 → 页标脏撤块、重合成的块以新交集 ACL 为草稿待重署名。
  审核台**没有独立的"撤发布"按钮**，这是设计态而非隐藏功能：撤块只有"源失效→fan-out→编译"一条执法链，
  给界面另开一条只有前端能看到的撤回口径＝第二本账。

## 在册缺口（诚实账）
- **补偿只有接口面与命令面，故意没有前端按钮**（M4-cj）：整批重放＝写侧批量动作，执行档不给"一键修复"这种一次点击就能改索引的
  可达性（同"复位只在 CLI"一条口径）。挂不挂界面、给哪个角色点＝待放行；`actor` 仍是调用侧自报署名（认证＝放行档），
  且补偿动作无贴切的 E3 冻结审计 kind，故**不落审计账**（专枚＝放行项㉑）。
- **硬断档下这道门会拦住最需要修的那一刻**：缺块 ≥100 时 B5 前置判定直接 503、零部分效果——这是闸语义的既有决定
  （与㉑族同源），阈值属禁改区不擅动。处置＝先按 `breakers.md` 把背离降到阈下（或人工分批核对）再回来跑补偿；
  只读入口不受影响，仍可反复刷新看账。
- **`withdraw`（撤发布）不作为独立动作存在**：见"回滚点"两条源失效路径；若将来要做"页级撤回不动源"，
  那是新的状态机迁移＋新的审计 kind＝E3 契约动作（人工 ADR + `contract_compat --update`＝放行项㉑），不是加个按钮。
