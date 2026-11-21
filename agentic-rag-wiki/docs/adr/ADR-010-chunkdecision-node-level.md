# ADR-010：决议记录由 doc 级单值判型重设计为 node 级节点段列表（FR-ING-7 结构切块的契约形状）

- 状态：采纳（2026-10-12，用户批复「替换式：决议变节点段列表（推荐）」＋「规则档全链（推荐）」）
- 关联：FR-ING-7（结构切块/放行项⑧）、D-1（判型与执行分离）、D-2（块型档位表）、D-3（判例预算）、F5（输入门限）、`src/platform/types/document.py`（`ChunkDecision`/`NodeDecision`）、`partitions/ingest/service/canonicalize.py`（`_to_nodes`）、`service/chunker.py`（`plan`/`apply`/`link_table_parents`/`merge_short`/`f5_gate`）、`service/budget.py`（`demote`）、`runtime/pipeline.py`、ADR-008（本条即其"放行项"里点名的 node 级重设计）。

## 背景
D-1 的口径是"判型与执行分离"：先出一份可归档的决议记录，切块执行＝对决议的确定性重放。但落地时的形状是 doc 级单值：

```python
ChunkDecision{profile, chunk_type, boundaries[], confidence, decider, doc_token_count}
```

一份文档只有**一个** `chunk_type`。这在纯散文上够用，一进混合语料就是结构病根：

1. **装不下**：同一份文档里 text 要聚合（400–800 token 带）、table 要原子（不可分，超阈退化成"表头+行组"）、code 要按函数/类边界不跨切——三种块型是三种正交策略，doc 级一个枚举值只能选一个，选了就对另外两类语料谎称已按档执行。
2. **判不了**：`boundaries[]` 是一串 token 偏移，它表达不出"这一组边界属于哪张表"。`canonicalize._to_nodes` 又只有标题/段落两种形状（规则档从不产 table/code 节点），所以第①级判型实际是空转的：`Chunk.chunk_type` 恒为 `text`，PRD 的四类块型只有一类有码支撑。
3. **链不上**：`Chunk.parent_chunk_id` 在册却无真实生产者（幻影字段族/gated-null）。表格退化成行组后，"这 6 块是同一张表"这条信息在 doc 级决议里无处落地，父子链就无从回填。
4. **对不齐**：D-3 降级件此前用 `model_copy(update={"decider": "default"})` 改写 doc 级一个字段；一旦一段是模型判、另一段是规则判，"整份降级"和"逐段判级"就是两套口径（本项目反复拦的**口径分叉族**）。

## 决策
1. **替换式重设计（不留幻影字段）**：`ChunkDecision` 去掉 doc 级 `chunk_type`/`boundaries`/`confidence`/`decider`，改为
   `ChunkDecision{profile, decisions: list[NodeDecision], doc_token_count}`，新增
   `NodeDecision{node_ids: list[str], chunk_type, confidence, decider}`。
   doc 级只保留两件事：`profile`（归档键与降级标记位的主档名）与 `doc_token_count`（D-3 判例预算的分母）。
   判型/判级**逐段记**，`node_ids` 顺序即重放顺序。
2. **`with_decider()` 取代 doc 级单字段改写**：D-3 降级＝逐段落写 `decider="default"`，不在 doc 级另设第二套判级口径。
3. **结构识别第①级（规则档）落在 `canonicalize._to_nodes`**：按空行分块＋代码围栏（围栏内空行**不**分块——围栏是结构边界），
   规则判型产 heading/paragraph/list/**table**/**code** 五种节点；表格判据采"宁可少判"（整块都是分隔结构才算表，
   散文里偶然一个 `|` 不算——误判＝把正文当原子结构块，检索不可分）。
4. **D-2 档位真执法**：`plan` 出逐段决议（text 聚合到 400 token 下界即在硬边界收束、800 为硬顶；table/code 永不与散文同段）；
   `apply` 按块型落地——text 超上界才句末软边界内切、table 原子（>2000 token 退化为 ≤800 token 行组且**每组重复表头**）、
   code 按 `def/class/@` 函数级不跨切；`merge_short` 把 <100 token 短节并入**同型**前一节（跨型不并、行组不参与并入）。
   口径澄清（真实 HTTP 跑出来的观察，非缺陷）：D-2 对 code 的承诺是**不跨切**（切口必落在函数/类头），不是"恒为一函数一块"——
   两个各 <100 token 的相邻函数会被 `merge_short` 并成一块（同型并入优先），块内 `def` 计数与源一致、不从函数中间切开。
5. **`parent_chunk_id` 首个真实消费者**：`link_table_parents` 在 `merge_short`/重编号**之后**回填（`chunk_id` 按序号派生，
   先链会指向重编号后已不存在的 id），并清掉 `extra` 里的临时 `group`/`group_index` 标记（临时态不外泄）。
6. **第②③级保持 fail-loud 占位**：`plan` 的 `decider` 恒为 `"rule"`，不冒充版面模型或 LLM 判型；版面/分类小模型与 LLM few-shot 需真实模型或云出口＝实机放行项，本刀不声称达标。
7. **块型透到出处面（决议不能只停在 A 区）**：`gateway/http.py` 的 `SourceView` 增 `chunk_type`/`parent_chunk_id` 两字段（类型引用 `types.document.ChunkType`，不在门面另写一份枚举），
   前端 `types.ts` 同步、出处卡片据此分排：表格/代码用等宽保留换行渲染（压成一行散文＝表格真相丢失），行组子块显示"同表行组 · 父块 N"提示。
   门面只透传切块结果，不自行判型也不填默认值——判型口径唯一在 `chunker`。

## 理由
- **形状必须承载策略**：判型是段落级事实，把它压成 doc 级单值＝契约表达不出要实现的东西，之后只能靠"实际上全是 text"来维持表面一致。
- **重放可审计**：决议逐段带 `node_ids`，重放结果与决议能一一对上；指向不存在节点的段＝输入不一致，由对账/B5 侧暴露，切块侧不臆造正文也不崩。
- **降级语义唯一**：`with_decider` 让"整份降级"与"逐段判级"是同一份数据的同一种改写，堵住 D-3 的第二套口径。

## 迁移与代价（**破坏性，如实写明**）
- **历史决议件不再可读**：决议记录归档键 `{doc_id}/{version}#decision` 的 JSON 形状已换（旧件带 `chunk_type`/`boundaries`、无 `decisions`）。pydantic 默认忽略未知字段，故旧件**能解析成空 `decisions`** → 重放得零块，属静默失真。迁移走既有通道而非新增机制：切块策略变更＝FR-ING-3 的 `pipeline.shadow_rebuild()`（按清单逐 doc 从 objectstore 重拉正文重放、影子建索引、原子切换、失败回滚；已实现且有 `tests/smoke/test_reindex_versioned.py` 契约锁，本刀时**CLI/HTTP 运维入口未暴露**＝可达性缺口在册、本刀不为迁移新开写侧入口〔**该缺口已由 M4-cm 于 2026-10-16 收口**：`POST /api/ingest/reindex`／CLI `admin reindex --confirm`，两者 1:1 委派且 **B0 总闸判门在全量重放之前**〕），重放时自然覆写新形状归档件；追平缺块走 `compensate()`/`_reindex_event()`（同语义、`origin=reconcile_backfill` 标注非源变更）。正文与 ACL 未变，无需外部供给。不做双形状兼容读（兼容读＝把两套口径同时留在系统里，正是要消除的病）。
- **E3 机检覆盖缺口（在册）**：`ChunkDecision`/`CanonicalNode`/`NodeDecision` 不在 `harness/schemas/validate.py:_MODELS` 与 `contract_baseline.json` 内，故本次一级契约形状变更 `contract_compat` **拦不住**。`src/platform/harness/*` 属禁改区（改基线需用户放行），本刀替代执法＝`tests/contract/test_structure_chunking.py::test_decision_contract_shape_is_node_level` 的形状锁（模型字段集相等）；把这三枚模型纳进 `_MODELS` 是后续待用户放行的护栏加固项。
- **面积**：`Chunk`/`CanonicalDocument`/`CanonicalEvent` 等 F1/E3 冻结件零改动（只加决议侧）；消费侧改动集中在 `chunker`/`budget`/`pipeline` 三个文件与三处契约测试。
- **档位带内收敛的副作用**：text 段收束点取在下界（400 token）而非上界，否则整篇文档会塌成一两块；问答置信度 `coverage = min(1, len(ranked)/topn_min)` 对单块文档必判低置信拒答（全环一次真实回归：568 token 文档塌成 1 块 → 拒答）。这不是调参而是 D-2 带内语义的直接体现。

## 可逆性
回滚＝恢复 doc 级 `chunk_type`/`boundaries` 并让 `_to_nodes` 只产 heading/paragraph、`apply` 走单型路径。这会同时丢掉混合语料的块型区分、表格父子链与 `parent_chunk_id` 的唯一消费者，属功能倒退而非等价替换；如需局部回退，只回退 `_table_chunks` 的 F5 退化（改回原子整表，超长表由 `f5_gate` 兜底截断并告警），决议形状不动。

## 落地边界（离线 vs 放行）
- **已落地**（离线代码路径 + 15 例契约测试＝`test_structure_chunking.py` 14 + `test_gateway_http.py` 出处面 1）：node 级决议形状与 round-trip、规则档第①级判型（含围栏空行不分块、偶然竖线不判表）、D-2 三型档位执法、F5 表格行组退化＋重复表头、`parent_chunk_id` 回填顺序锁、<100 token 同型并入、重放确定性、D-3 降级件逐段 `default`、决议契约形状锁、块型经 `/api/source` 透到出处面并在前端分排。全环 **600 tests / 0 failures / 0 errors / 2 skipped（=598 passed）**，ruff、`mypy src`（123 files）、10 道 harness 门全绿。
- **真实 HTTP 通道核验**（一次性脚本走 dev/fake 装配＋合成语料，用后即删、不落仓）：混合样张经 `POST /api/ingest` 入库后，13 条判据全过——决议件按 `{doc}/1#decision` 归档且是 node 级三字段、逐段出 text/table/code 三类且 `decider` 全 `rule`、`/api/source` 逐 seq 读回的块型与正文形状各按 D-2（600 行表→7 个 ≤800 token 行组、每组重复表头、6 块带真实 `parent_chunk_id`；两函数代码块未被从中间切开；散文 2 块最大 430 token）。这条核验只证明**结构契约在真 HTTP 往返上成立**，不证明版面识别或检索质量。
- **放行项/残余**：结构识别第②级（版面/分类小模型；Word 直读 docx 原生 XML 结构）与第③级（LLM few-shot 兜底）＝需真实模型或云出口；四类语料（散文/表格/代码/图文混排）的人工抽检与真实 PDF 版面回捞属真机评测（金标 `tests/eval/` 只读）；D-3 判例预算的真实 token 计量随云出口定档。本刀不据此声称版面识别或人工抽检已达标（R4/A2）。
