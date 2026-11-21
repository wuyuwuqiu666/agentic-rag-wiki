# 可达入口总矩阵（代码 ↔ 处置页 的单一源，`runbook_check` 机检）

范围口径：本表只登记**执行档真实可达的入口**（HTTP 门面 + CLI 命令面）与其处置页归属，**不重述**任何一页的操作步骤——
步骤归各页，本表归"谁管哪条入口"。新增入口未进本表＝`runbook_check` 红（M4-cl），
本表引用的页名不存在＝同一道门红（防"矩阵里指路、路上没有页"的幻影处置页）。

判据来源＝代码，不由人手抄：HTTP 行取自 `src/platform/gateway/http.py` 的路由装饰器，
CLI 行取自 `src/platform/gateway/cli.py` 的子命令注册位。两条硬口径（权威表述在 `admin_observability.md`）：
**写侧动作一律要显式 `confirm`；复位唯一入口＝CLI `admin reset`，HTTP 面故意不暴露**（执行档无鉴权，裸暴露＝任何人可解 B0 冻结）。

## HTTP 门面（`/api/*`）

| 入口 | 面 | 处置页 | 一句话口径 |
|---|---|---|---|
| `GET /api/health` | 读 | `admin_observability.md` | 只证门面活着＋当前档位；`quality=echo-not-model`＝未接真模型（放行项①），不是质量结论 |
| `GET /api/scope` | 读 | `scope_409.md` | 可见域由后端按 C 区画像划分；画像未建＝409 且不给任何域（冷启动不放大权限） |
| `POST /api/ask` | 读 | `breakers.md`、`admin_observability.md` | 拒答优于错答；降档话术与漏斗读数在此解释，越权内容在 App 内 pre-filter 前就被挡住 |
| `POST /api/ingest` | 写 | `ingest_retire.md`、`breakers.md` | 原始文本入库；B0 冻写则整批 503，B1 删除占比闸同面执法 |
| `POST /api/ingest/file` | 写 | `ingest_office_legacy.md`、`ingest_scan_ocr.md`、`chunking_structure.md` | 单文件字节→解析→切块；格式不认/需 OCR/结构退化各按对应页处置，不谎称能解 |
| `POST /api/ingest/dir` | 写 | `ingest_directory.md` | 整目录批量；未配允许根目录 501、越界 403、覆盖产物目录 422 |
| `POST /api/ingest/change` | 写 | `ingest_retire.md`、`breakers.md` | 增量变更（upsert/delete）；"源删除"走此道，下架即三路同清 |
| `POST /api/ingest/restore` | 写 | `ingest_retire.md`、`restore.md` | 窗内墓碑回灌；`recoverable=false`＝过保留窗，与正文缺失是两种不可恢复 |
| `GET /api/ingest/deletes` | 读 | `ingest_retire.md` | 下架台账读回（可回滚面＝窗内 ∧ 正文在，两者缺一即不可找回） |
| `GET /api/ingest/reconcile` | 读 | `admin_observability.md`、`wiki_review.md` | 对账只读入口，**只读不传感**（观测端不得自己成故障源） |
| `GET /api/ingest/acl-history` | 读 | `ingest_retire.md` | 受众变更前值账读回（从哪些域收到哪些域、落在哪档）；同样只读不传感，空列表＝从未改过受众而非没记上 |
| `POST /api/ingest/compensate` | 写 | `admin_observability.md`、`wiki_review.md` | 补偿＝整批重拉，无逐块参数、须 `confirm`；写侧修复不包装成 GET 里的一键。**先判 B0 总闸、再判 B5**：B0 冻结期一律 503、零重放零报告，与这一批有没有债无关（#14 已收口＝M4-cv，读法见 `admin_observability.md`） |
| `POST /api/ingest/reindex` | 写 | `restore.md`、`breakers.md` | 影子全量重建＋别名原子切换；B0 冻结则整批 503 且零部分效果。`applied=false` ∧ `rolled_back=true`＝切换失败已退回旧别名，**不是系统坏了** |
| `POST /api/ingest/alias/revert` | 写 | `restore.md` | 切换**成功后**人工判定新索引劣化时整域切回旧别名（§10 高危回滚点）；同名 422 不触库，dev 单 store 不核验保留窗（真·72h＋切回审计＝放行项②） |
| `GET /api/source` | 读 | `ingest_retire.md`、`scope_409.md` | 出处正文读回；404＝块已下架或从未入库，403＝越可见域，二者都不是"系统坏了" |
| `POST /api/wiki/compile` | 写 | `wiki_review.md` | 脏页增量编译产出草稿，草稿不等于已发布；租约消费＝取用后仍计在租，B0 拉闸期 503 且零取用（冻结是推迟不是丢活儿，复位后再点即补齐） |
| `POST /api/wiki/review` | 写 | `wiki_review.md`、`scope_409.md` | 人审发布门：状态机判定只在 `wiki/service/review.py`，门面不判也不改判 |
| `GET /api/wiki/pages` | 读 | `wiki_review.md` | 页与状态读回（`published`/`stale`/`retracted`） |
| `GET /api/admin/funnel` | 读 | `admin_observability.md` | 命中率四层归属读数；分母不含拒答 |
| `GET /api/admin/pool` | 读 | `admin_observability.md` | B2 分池三本独立账读数 |
| `GET /api/admin/audit` | 读 | `admin_observability.md`、`restore.md` | 审计事件读回（保留期与物理删除口径见 `restore.md`） |

## CLI 命令面（`python -m src.platform.gateway.cli …`）

| 入口 | 面 | 处置页 | 一句话口径 |
|---|---|---|---|
| `cli ask` | 读 | `breakers.md`、`admin_observability.md` | 与 HTTP 问答同一 façade，同一 ACL 口径 |
| `cli ingest` | 写 | `ingest_directory.md`、`ingest_retire.md` | 单文件/目录入库的程序化入口，缺配置即退出码 1（fail-loud） |
| `cli admin` | 读 | `admin_observability.md` | 运维读回子命令组的根，本身不产生动作 |
| `cli admin audit-replay` | 读 | `admin_observability.md`、`restore.md` | 按保留策略回放/核算审计账 |
| `cli admin pool` | 读 | `admin_observability.md` | 分池预算读回 |
| `cli admin funnel` | 读 | `admin_observability.md` | 消费漏斗读回 |
| `cli admin degrade-plan` | 读 | `admin_observability.md`、`breakers.md` | 只出 SLO 降档判定，执行降档由在飞容量与阶梯驱动（两条信号不混读） |
| `cli admin reconcile` | 读 | `admin_observability.md`、`wiki_review.md` | 对账读回，`sense=False` 语义＝不因此次观测再记一次传感 |
| `cli admin acl-history` | 读 | `ingest_retire.md` | 与 `GET /api/ingest/acl-history` 同一函数、同一本账；`doc_id` 必填，读回不改受众也不传感 |
| `cli admin compensate` | 写 | `admin_observability.md`、`wiki_review.md` | 补偿须 `--confirm`；判门在写侧之前（先 B0、再 B5），一次动作只传感一次；B0 不再取决于“有没有块要重放”（#14 已收口＝M4-cv） |
| `cli admin reindex` | 写 | `restore.md`、`breakers.md` | 与 HTTP `POST /api/ingest/reindex` 同一函数、同一次 B0 判门；**故意无干跑档**，想先看清账＝只读的 `admin reconcile` |
| `cli admin alias-revert` | 写 | `restore.md` | `--from-alias/--to-alias` 须 `--confirm`；同名或后端抛错＝退出码 1，不把回退失败说成已切回 |
| `cli admin reset` | 写 | `breakers.md`、`admin_observability.md` | **复位唯一入口**：双人签署在 CLI 可达、HTTP 面故意不暴露；单人签署即拒 |

## 本表管不到的（诚实边界）

- 前端视图（`src/platform/gateway/web/`）不是独立入口层：它逐个调用上表的 HTTP 行，口径以上表为准；
  故意没有前端"复位"按钮（同上写侧口径），补偿也**没有**前端按钮。
- 影子重建与别名切回（`IngestPipeline.shadow_rebuild`/`revert_alias`）自 **M4-cm** 起在上方四行在册（HTTP 两条＋CLI 两条）；
  执行档**不给前端按钮**——全量重放与整域切回都是写侧动作，确认门留在命令面/接口面，与 `compensate` 同一口径。
- 真实源连接器、真机压测、真金标等属 `AGENTS.md §8` 实机放行项清单，不属"执行档可达入口"。
