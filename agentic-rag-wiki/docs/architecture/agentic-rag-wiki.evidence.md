# 证据索引（逐节点 → 真实文件）

取证时间 2026-10-16（M4-ct 复跑刷新下面四行；上一轮 2026-10-14，其余行未复核）；口径＝**行级可核**（每条都能用 `grep`/`ls` 复核）。置信档：high＝直接读到代码；medium＝多处部分证据一致；low/unknown＝推断或未落地。

## 结构事实（先核过的分母）

| 事实 | 读数 | 取证方式 |
| --- | --- | --- |
| src Python 文件数 | 124 | `find src -name '*.py' -not -path '*/web/*'` |
| HTTP 端点数 | 21 条路由装饰器 | `grep -cE '@app\\.(get\\|post)\\(' src/platform/gateway/http.py` |
| CLI 子命令 | ask／ingest／admin{audit-replay, pool, funnel, degrade-plan, reset, reconcile, compensate, acl-history, reindex, alias-revert}＝**13 条注册位**（含 `admin` 根） | 递归遍历 `cli._build_parser()` 的 `_SubParsersAction.choices`（同 `tests/smoke/test_cli_invocations_documented.py` 判据口径） |
| 可达入口总矩阵 | 34 行（HTTP＋CLI） | `python -m src.platform.harness.runbook_check`（与代码注册位 1:1） |
| 前端视图组件 | 10 个 .tsx | `ls src/platform/gateway/web/src/components/` |
| 熔断规则 YAML | 8 个（b0/b1/b2/b3/b3_throughput/b4/b5/b5_missing） | `ls src/platform/harness/breaker_rules/` |
| E3 契约基线模型 | 15 | `harness/schemas/validate.py:_MODELS`（M4-ck 后） |
| 组合根 Container 字段 | 22 | `src/platform/gateway/composition.py` |

## C4 节点证据

| 节点 | 落点 | 置信 |
| --- | --- | --- |
| Web 前端 | `src/platform/gateway/web/`（Vite+React+TS+Tailwind v4+Motion），`http.py` 在 `web/dist` 存在时同源挂载 | high |
| Gateway 应用 · 组合根 | `platform/gateway/composition.py`（docstring 自称"唯一注入点"） | high |
| · HTTP 门面 | `platform/gateway/http.py` | high |
| · CLI 命令面 | `platform/gateway/cli.py` | high |
| · App / Admin façade | `platform/gateway/app.py` / `admin.py` | high |
| · PolicyGate / InflightTracker | `platform/gateway/policy_gate.py` / `inflight.py` | high |
| · AuditAdapter | `platform/gateway/audit_adapter.py` | high |
| A 区 摄取与问答 | `src/partitions/ingest/{repo,service,runtime}` | high |
| · IngestPipeline | `ingest/runtime/pipeline.py` | high |
| · canonicalize/chunker/intake | `ingest/service/{canonicalize,chunker,intake}.py` | high |
| · PrecedentLedger | `ingest/service/budget.py` | high |
| · admission + dlp | `ingest/service/{admission,dlp}.py` | high |
| · CanonicalInventory（双册） | `ingest/repo/canonical_inventory.py` | high |
| · DeleteLedger（墓碑） | `ingest/repo/delete_ledger.py` | high |
| · SourcePort / DevSource | `ingest/repo/source.py`（真实连接器＝㉒，low） | high（契约）/unknown（实现） |
| · reconcile/compensate/reindex | `ingest/service/{reconcile,reindex}.py` + `pipeline.py` 内 compensate | high |
| · AnswerAgent | `ingest/runtime/ask.py` | high |
| · service/ask 九件 | `ingest/service/ask/{cache,citation,cost,funnel,ladder,neighbor,planner,policy,fusion}.py` | high |
| · IngestionPort | A 区暴露的 Protocol（B 区唯一回写通道） | high |
| B 区 wiki 编译 | `src/partitions/wiki/runtime/loop.py` + `service/{compile,review,triage}.py` + `repo/pages.py` | high |
| C 区 记忆与画像 | `src/partitions/memory/runtime/loop.py` + `service/{audit_sink,domain_scope,portrait_policy,param_proposal,report}.py` + `repo/portrait_store.py` | high |
| 平台平面 | `platform/types/*.py`(6) · `config/settings.py` · `providers/`(28 文件、`ports.py` 15 个 Protocol) · `breaker/{engine,rules,pool,slo}.py` · `harness/`(10 门) | high |
| 向量/词法/缓存/对象库/队列/审计/画像/产物 | `providers/vectorstore_{memory,pgvector,milvus}.py`、`bm25_dev.py`、`cache_memory.py`、`objectstore_{fs,s3}.py`、`mq_{pgqueue,kafka}.py`、`memory/service/audit_sink.py`、`artifact_dir` 下 reconcile/portraits | high |
| 云 LLM / 云 Embedding·Rerank / 本地模型 / RapidOCR / 本机 Office | `providers/{llm_cloud,llm_local,llm_router,embedding_cloud,embedding_local,scan_ocr_rapid,fileparser_dev}.py` | high |
| 真实源系统（SharePoint/OA/网盘/Git） | 无实现文件，仅 `SourcePort` 契约＋`DevSource` 内存档 | **unknown＝放行项㉒** |
| 企业档 PG（唯一事实源·权限·审计分表） | `pg_dsn` 配置在、双存储对账真口径未接线 | **low＝放行项②** |
| C 区自动写安全参数 | 无该通道（跨区三禁③） | **不存在，图上已标未实现** |

## 流程节点证据（要点）

| 流程断言 | 执法点 | 置信 |
| --- | --- | --- |
| 判门在写侧之前、一次动作只传感一次 | `pipeline.py` 内 `guard.check` 前置于重放/写入循环；测试 `test_compensate_entry.py`、`test_delete_restore_tombstone.py::test_restore_senses_b5_exactly_once` | high |
| 只读不传感 | `reconcile(sense=False)` 单一计算路径开关；`test_reconcile_entry.py` | high |
| 两路索引同生同灭 | `_purge` 三路同清（向量/词法/缓存）；M4-cc/M4-cd 测试 | high |
| 可回滚面＝窗内 ∧ 正文在 | `pipeline.py:_has_body`（单点判据）＋ `http.py` `/api/ingest/deletes` 三分账 | high |
| 缓存无 TTL、四条清缓存路径 | `_purge` / `admit` / `acl_change` / `process_document`（按源块域）；`test_semantic_cache.py` | high |
| pre-filter 唯一解析点、禁 post-filter | `memory/service/domain_scope.py` + 门面 `_domains`/`_reader_domains`；`test_writeback_lifecycle.py` 模块不变式 | high |
| 阶梯只减功能不加失败 | `service/ask/ladder.py` + `AnswerAgent(degrade_level=...)`；`tests/contract/test_degrade_ladder*` 9 例 | high |
| ①层缓存命中不被阶梯拦 | `test_level_two_still_serves_semantic_cache_hit` | high |
| 降级件不入缓存 | `test_level_two_wiki_direct_answer_never_populates_cache` | high |
| 命中率分母不含拒答 | `service/ask/funnel.py` `assess`/`refused` | high |
| 云主档失败回退本地且不判 B3 | `providers/llm_router.py` + `composition.py:_on_fail` | high |

## 未验证 / 会误读图的地方

- 图上 `EmbeddingPort→本地模型` 一条边是**装配形状**；执行档默认 dev 哈希档，真机数字属放行项①。
- `dev 单进程` 的分池账本为进程内账，跨进程共享属企业档（放行项⑰）。
- 前端文案改动（`RetireView.tsx` 等）本轮**未在浏览器目视确认**（browser-use 视口不可用），已如实登记在 AGENTS §8 M4-cn 段。
