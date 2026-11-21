# Runbook：文档下架与恢复（FR-ING-2 / §10 墓碑保留期 / M4-ce 1a / M4-cq）

范围口径=执行档可达入口的**操作步骤与读数解释**。熔断占比语义**不在本页**（唯一口径=`breakers.md` 的 B1 条，本页只指路不重述，防两张皮）；备份容灾口径见 `restore.md`。

## 入口清单（唯一可达面，勿另找）
| 动作 | HTTP | 前端 | CLI |
|---|---|---|---|
| 下架 | `POST /api/ingest/change`（`kind:"delete"`） | 左栏「下架与恢复」`RetireView.tsx` | **无**（命令面只有 `ask`/`ingest`/`admin`） |
| 改可见域 | `POST /api/ingest/change`（`kind:"acl_change"`） | 左栏「改可见域」`RescopeView.tsx`（M4-ci；与下架同一端点、同一 `confirm` 门） | 无 |
| 待恢复墓碑账 | `GET /api/ingest/deletes` | 同上（选单） | 无 |
| 误下架回滚 | `POST /api/ingest/restore` | 同上（逐条按钮） | 无 |
| 受众变更前值账读回 | `GET /api/ingest/acl-history?doc_id=…[&version=]` | 无（只给命令面/接口面，页面上不做成第二本账的视图） | `cli admin acl-history DOC_ID [--version]` |

## 下架（delete）
1. **先看闸**：`Settings.shadow_breakers` 含 B1（dev 缺省 `"B1,B5"`）时本入口可用；转全硬断档后单文档下架会被 B1 判停 503，**且零部分效果**（索引未动、墓碑未记）——判停是好事，等于什么都没发生，可直接重试。豁免与否属阈值语义决策＝§7 禁改区（放行项㉑），**不要顺手改 `b1.yaml`**。
2. **发请求**：`{doc_id, kind:"delete", version?, actor, confirm:true}`。`confirm` 是破坏性动作的显式确认位；`kind` 只收 `delete|acl_change`——`upsert`/`domain_move` 在执行档零消费点，schema 直接 422 拒收（接了再无视＝幻影产品）。`version` 缺省=整 doc 全版本，给定=只该版本。
3. **读回执**（`apply_changes` 的账 + 门面署名）：`total`（本批条数，本入口恒 1）/`applied`·`blocked`·`failed`（逐条结果三计数，见下节）/`deleted`（索引移除块数）/`cache_evicted`（按域清掉的缓存条数）/`recoverable`（进可恢复账的条数＝窗内**且**正文仍在，M4-cn⑧）/`hint`。两条如实文案：`recoverable>0` 承诺保留期内可回滚；`recoverable=0` 表示无登记信封或正文指针缺失，**不做恢复承诺**。`reindexed` 在本执行档**恒 0**（HTTP 不收 upsert），不是漏做。
4. **核账实**（三条都过才算下架成功）：① `GET /api/ingest/deletes` 出现该 doc 的墓碑；② `GET /api/source?chunk_id={doc}/{ver}#{seq}` 对已删块 404（该端点不区分"不存在/越权"，防探测）；③ 问答不再引该块。**核验坑**：同问题同 ACL 会命中①层语义缓存而零检索，须换问法再看检索账。
5. **补撤权威块**：若该 doc 是已发布 wiki 页的源，A 区里的 W 权威块要跑一轮 `POST /api/wiki/compile` 才撤下（`_invalidate` 只在 `WikiLoop.run_once()` 消费 fan-out 时执行）；回执 `deleted` 只计 A 区被删块，**不含**撤下的权威块数。审核台口径见 `wiki_review.md`。

删除的**唯一执行点是 `IngestPipeline._purge`**＝向量索引 + 词法(BM25) 索引 + 语义缓存按域失效三路同清。任何新增失效路径必须走它，否则出现"词法路仍供已删块"或"缓存 TTL 期内继续供答案"的账实背离。

## 逐条账：一条失败不吞整批的账（M4-cr／全生命周期审计 #17 补录）
- **为什么要有这本账**（实测形态，不是假想）：`apply_changes` 旧行为＝批内第 k 条抛错时整批直接上抛、**回执为零**，
  而前面几条的效果（两路索引已清、清单已改账、墓碑已记、fan-out 已广播）**已经落地**——调用方只拿到一句异常文本，
  事后无从知道"哪几条已生效"。同一条口径也管住了"正文取不到就把空正文重放"的旧写法（回执照记 `reindexed=1`、
  还向 B 区广播一条并不存在的变更＝幻影效果）。
- **三计数与条数恒等**：`applied ＋ blocked ＋ failed ＝＝ total`（一条都不许从账面掉出去）；`total`＝**真实条数**
  （空批＝0，不写成 1；旧写法一处两用把"没有变更"报成"1 条变更"，目录批量面板照它念给用户）。
- **blocked 的三种**（都是零效果、都给可指原因，不冒充已处理）：① 零在册块的 delete（清单无登记）；
  ② `upsert` 的正文不在册或正文为空（空正文重放＝把内容清零伪装成更新，清零请用 `kind=delete`）；
  ③ 执行档没有消费点的 `kind`（如 `domain_move`，要落这一类动作须 ADR＝放行项㉑）。
- **failed 过门面＝500 且把逐条账原样带出**：`detail` 给人读、`receipt.items` 给机器读，每行
  `seq/doc_id/version/kind/outcome/detail`。门面**不判定、不重试、也不改判"部分成功"**（本层零判定，与 BreakerTripped
  同一处置：一处渲染、口径唯一）。成功批**不带** `items`——明细只随异常走，回执计数面保持 int（`pull`/`/api/ingest/dir`
  的 `dict[str,int]` 面不受影响）。
- **`failed` ≠ 零效果**：单条 delete 内部三步（摘两路 → 记墓碑 → 清清单）**不原子**。失败行的 detail 点名已落地部分
  （"已摘两路索引 N 块、记墓碑 M 条"）并给两条出路：先 `GET /api/ingest/reconcile` 看背离债，
  再 `POST /api/ingest/compensate` 按清单指针重放补齐。真做成原子＝需事务性双索引＝真实 PG＋Milvus 档（放行项②），
  本刀不假装给 dev 造一个。
- **补偿的代价要读对**：compensate 把债补齐＝那条被摘掉的块**回来了**（清单说它还在，补偿按清单重放），
  所以补齐后必须**再发一次 delete**。判据：`tests/contract/test_change_accounting.py`（11 项）+ 真实 HTTP 走查都钉住了
  "部分效果如实留债 → 补偿清零 → 再删才成 applied"这三步。

## 恢复（误下架回滚）
- 先读账：`GET /api/ingest/deletes` → `{now, items, recoverable, body_missing, expired}`。`items` 逐条十字段：`doc_id/version/domain/chunk_ids/deleted_at/retain_until/seconds_left/recoverable/has_body`。
- **两种不可恢复是不同回事，界面与处置都按实区分**：`recoverable=false`＝超出保留窗口；`has_body=false`＝正文已不在 objectstore（`store_key` 指针缺失或在册但实体已丢）。后者即便在窗口内也找不回来，恢复必失败。
- **三个汇总数与恢复作业回执同一口径（M4-cn⑧）**：`recoverable`＝窗内**且**正文仍在＝真可回滚面；`body_missing`＝窗内但正文已丢；`expired`＝超窗。窗内行数＝`recoverable + body_missing`，别把两者混成一个"还能找回"的期望。
- 回滚：`POST /api/ingest/restore {doc_id, actor, confirm:true}` → `{restored, recoverable, body_missing, expired_unrecoverable}`。**必带 `doc_id`**：缺省=全量重放，会把别人的墓碑一起带回。
- 恢复语义=从 objectstore 重拉正文**重放**，同 chunk_id→upsert 幂等复现（非新内容、不产生新版本号语义）；`restore_deletes` 尾部自跑 B5 对账（`reconcile_missing_chunks`）——恢复后账实背离当场交闸，不静默。**一次动作只传感一次**（M4-cn⑦）：内部那次读数走 `sense=False`，刷新页面或重放同一批不会把 B5 观察记成两笔。
- 过窗计入 `expired_unrecoverable`、窗内正文真丢计入 `body_missing`，一律**不进 `recoverable`**＝不臆造"还能一键找回"（查 objectstore 是 `body_missing` 的处置，接受找不回才是 `expired_unrecoverable` 的处置）。

## 改可见域（acl_change）
`{doc_id, kind:"acl_change", acl_before, acl_after, confirm:true, actor}`。回执字段读法：

| 字段 | 含义 | 判读 |
| --- | --- | --- |
| `acl_rewritten` | 已按新域重写的**源块**数（两路索引＋清单同时换域） | `<` 该 doc 在册块数＝有块没换净，看下一行 |
| `acl_pending` | 正文指针缺失、本轮换不了的块数 | 非 0＝真孤儿，须先补正文（`/api/ingest` 重投）再改域 |
| `acl_retired` / `retired_acl_docs` | 收紧落在**已下架版本**上的条数（按 `(doc_id,version)` 计） | 非 0 不是失败：索引当时没有它，改的是受众账；恢复即按新域供答（M4-cq） |
| `cache_evicted` | 语义缓存被剔条目数（含换域途中"先摘旧块"顺带清的） | 报 0 而确有命中＝少报，属缺陷 |
| `effects_done` / `effects_pending` | 已兑现效应②③④ / 未兑现项 | `effects_pending` 恒含"PG 权限行（效应①＝放行项②）"，别当已改完权限模型 |

受众收紧**分两段**，回执 `hint` 如实写明：源块即刻换域（旧域检索与 `/api/source` 都读不到）；但该 doc 若发布过权威页，
**那张已发布的权威块在下一轮编译前仍按旧受众可读**——关窗动作＝改完立刻 `POST /api/wiki/compile`（界面「跑一轮编译」），
撤块后须**重新署名发布**才带着新交集 ACL 回索引＝受众变更由人签字，系统不自动复权。
诚实边界：改可见域的前端表单＝左栏「改可见域」（`RescopeView.tsx`，M4-ci），它只把上面这条 body 原样发出去，
一个数都不自己算、回执里的账一条不替后端圆场；`acl_pending>0` 在界面上砖红标出（＝有块没换净，不是"已经改好"）。
**批量改域仍无入口**（表单一次一份文档；目录级批量只灌新文档＝`ingest_directory.md`）。

## 收紧不丢：受众前值账（M4-cq／全生命周期审计 #3）
- **修前为什么会丢**（实测形态，不是假想）：`acl_change` 的目标集只取 A 区清单，文档一经下架清单里就没它了→
  回执四个数全 0、索引没动、也没任何地方留"从 fin 收到 hr"；而墓碑存的是**下架那一刻**的受众，
  `POST /api/ingest/restore` 按墓碑重放＝把收紧前的宽受众重新灌回两路索引。真跑一遍＝旧域读者拿回引用、
  新域读者反而读不到（`tests/contract/test_acl_before_ledger.py` 第一条钉住这条）。
- **现口径**：每条 `acl_change` 逐版落 `AclAudienceLedger`（前值→新值＋落点），恢复／补偿／改域**三路共用的唯一重放口**
  `_reindex_event` 只问这本账的当前值，故收紧一经入账就不会被任何后续重放撤销。
- **`applied_to` 三档读法**：`inventory`＝在册块两路已改写；`retired`＝文档当时已下架，改的是账；
  `pending_body`＝正文缺失、索引当时改不到。后两档**不新增也不消除 B5 背离债**，只说明"收紧已存续、等重放兑现"。
- **前值一律取实值，`acl_before` 在改域路径零消费点**：在册版取清单信封的 `acl_tags`，下架版取**账上最新域**（连续两次
  收紧要接成链：第二次的前值＝第一次的结果），账上无记录时取墓碑信封里的下架时受众。首版实现把请求自述当下架版
  前值兜底，真 HTTP 核验一报假 `acl_before` 就把账写歪成「从 ops 收到 hr」——已改，并由
  `test_retired_tightening_records_real_before_and_chains` 钉住。
- **不改写墓碑**：墓碑留"下架那一刻的事实"，受众账留"当前应当的域"，两本账各记各的（改写墓碑＝账实分叉）。
- 读回＝`GET /api/ingest/acl-history` / `cli admin acl-history DOC_ID`，与 `admin reconcile` 同一条**只读不传感**纪律；
  空列表＝该文档从未被改域，不是"没记上"。
- 边界：账＝进程内 stand-in，重启即空＝**放行项②**（真权限行与跨进程持久同源）；"谁改的"不落审计账＝**放行项㉑**。

## 回滚点
- 本入口的不可逆边界＝墓碑账。内存 `DeleteLedger` 是 stand-in：**30 天保留期与跨进程持久＝PG 放行项②**，进程重启后墓碑账失（下架动作本身仍已生效，只是不再可一键回滚）。
- 真要"下架错了又找不回"：唯一出路=重新入库（正文仍在 objectstore 者可 `shadow_rebuild` 全量重放，见 `restore.md` 回滚点条）。
- 下架该源后它的**未发布草稿升不了权威**（M4-cd 依据存活门：`POST /api/wiki/review` 的 approve 返回 409「依据已失效」，页停在 `in_review`）；按 §10 恢复该源，同一草稿即可照常发布。发布侧口径唯一源＝`wiki_review.md`。

## 在册缺口（诚实账，勿当已完成）
- **`actor` 仅回显、不落审计**：`AuditEvent.kind` 是 E3 冻结 9 值 Literal，无贴切成员表达"谁下架了什么"；挪用 `breaker_trip`/`acl_denied`/`quarantine` 会把运维动作混进安全语义、污染尺子。专枚 kind＝人工 ADR + `contract_compat --update`＝**放行项㉑**。
- **逐条账只随异常带出、不持久**（M4-cr）：成功批回执仍是纯计数（不掺 `items`，保护 `pull`/`/api/ingest/dir` 的 `dict[str,int]` 面）；500 那份 `receipt.items` 也**不落盘**——同上，逐条结果需要的是"当场可指"，永久账需要的是新的 kind＝放行项㉑。进程退出后这批的逐条明细只能靠 `reconcile` 反推。
- **`upsert` 若将来过门面**：其 blocked/failed 明细当前无处可读（本执行档 HTTP 只收 `delete|acl_change`，二者的 blocked 已由各自计数行可见），届时须一并暴露 `items`，不要让"批很大、只知计数不知哪条"回来。
- B0 拉闸期间撤块动作被同一道写侧闸拒绝，复位后须由重编译补撤。
