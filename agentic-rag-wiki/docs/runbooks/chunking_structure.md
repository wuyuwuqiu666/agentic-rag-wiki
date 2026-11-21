# Runbook：结构切块与决议记录（FR-ING-7 D-1/D-2，规则档＝第①级；M4-cg / ADR-010）

范围口径=执行档（单机、离线可跑）。本文件讲三件事：**一份文档会被切成什么样的块**、**决议记录怎么看**、
**改档位或换判型时怎么安全落地**。本刀只兑现结构识别**第①级（规则档）**，第②③级（版面/分类小模型、LLM few-shot）
需真实模型或云出口＝放行项⑧ 剩余半边，本文件不声称版面识别或四类语料人工抽检已达标。

## 一、链路形状（判型与执行分离）
```
Document.raw_text
  → canonicalize._to_nodes   第①级规则判型：heading/paragraph/list/table/code 节点（D-1 的输入）
  → chunker.plan             逐节点段出决议 NodeDecision{node_ids[], chunk_type, confidence, decider="rule"}
  → 归档 {doc_id}/{version}#decision（objectstore，D-1）
  → chunker.apply            确定性重放：块型各按 D-2 档位
  → chunker.merge_short      <100 token 短节并入同型前一节（重编号）
  → chunker.link_table_parents 表格行组父子链（必须在此序：重编号之后）
  → chunker.f5_gate          超下游 input 上界（8192 token）截断并返回被截断 id → 审计 provider_fail_loud
  → EmbeddingPort            向量只接收切好的块（不参与切分决策）
  → /api/source（SourceView）块型与父子链透出处面：前端出处卡按 chunk_type 分排、行组显 parent_chunk_id
```

## 二、出厂档位（`chunker.py` 常量，改它=改 D-2 口径）
| 常量 | 值 | 含义 |
|---|---|---|
| `_CHARS_PER_TOKEN` | 4 | 执行档 token 口径 `len(text)//4`，与 canonical/pipeline 同源 |
| `_TEXT_MIN_TOKEN` | 400 | text 段**达下界即在硬边界收束**（段落/标题/列表=硬边界） |
| `_TEXT_MAX_TOKEN` | 800 | text 硬顶：单节点超它才按句末软边界内切 |
| `_TABLE_DEGRADE_TOKEN` | 2000 | 表原子优先；超它退化为「表头+行组」 |
| `_TABLE_GROUP_TOKEN` | 800 | 每个行组上限，**每组重复表头** |
| `_MIN_TOKEN_MERGE` | 100 | 短节并入**同型**前一节（跨型不并、行组不参与并入） |
| `_F5_DOWNSTREAM_LIMIT_TOKEN` | 8192 | 最紧下游模型 input 上界（本地 reranker） |

**为什么取下界而不是上界收束**：问答置信度 `coverage = min(1, len(ranked)/topn_min)`，整篇塌成一两块的文档几乎必被判低置信拒答
（全环一次真实回归：568 token 文档→1 块→`refused=True`）。400 token 下界让块落进 400–800 带内，既守 D-2 又保检索面。

## 三、怎么看决议记录（重放与对账入口）
决议记录是 `{doc_id}/{version}#decision` 的 JSON，形状（ADR-010，node 级）：
```json
{"profile": "rule_structure_v1",
 "decisions": [{"node_ids": ["n0"], "chunk_type": "text", "confidence": 1.0, "decider": "rule"},
               {"node_ids": ["n2"], "chunk_type": "table", "confidence": 1.0, "decider": "rule"}],
 "doc_token_count": 123}
```
读回与核对（本机执行档）：
1. 正文与决议都在 `APP_ARTIFACT_DIR`（默认 `workspace/`）下的 objectstore 目录里；键即 `{doc_id}/{version}` 与 `{doc_id}/{version}#decision`。
2. 重放核对：`ChunkDecision.model_validate_json(raw)` + `chunker.apply(canonical, decision)`——同输入必得同 `chunk_id` 序列。
3. `decisions[].decider` 只会出现 `rule`（本刀）/`default`（D-3 越限降级，逐段落写）。看到 `layout_model`/`llm`＝第②③级已接入，须另有真实供给与 ADR。
4. 某段 `node_ids` 指向 canonical 里不存在的节点 → 重放**跳过该段**（不臆造正文），不一致由 B5 对账侧暴露，不在切块侧静默修补。

## 四、运维动作
**改档位（profile 参数，属 D-2 口径变更）**
1. 改 `chunker.py` 常量 → 必改的在册处：PRD 附录 E.1 D-2 行、E.2「块型默认档位表」、本表。三处不同步=口径分叉。
2. 重跑 `python -m pytest tests/contract/test_structure_chunking.py tests/contract/test_chunk_policy.py -q`。
3. 切块结果变了就要重建索引：`POST /api/ingest/reindex`（body 须 `confirm: true`）或 CLI `python -m src.platform.gateway.cli admin reindex --confirm`（M4-cm 起可达；两者 1:1 委派 `IngestPipeline.shadow_rebuild()`＝FR-ING-3 影子重建→原子切换→失败回滚）。
   **读数与前置**：B0 拉闸期整批 503 且零部分效果（判门在全量重放之前）；`applied=false ∧ rolled_back=true`＝回滚成功、旧别名仍在服务，不是新故障；切完用 `GET /api/ingest/reconcile` 复核账实（只读不传感）。执行档**无前端按钮**（批量写侧动作，见 `restore.md`／`entry_matrix.md`）。

**第③级 LLM 兜底接入（放行档）**
- 注入缝已就绪：`IngestPipeline(chunk_fallback=...)`，契约 `(CanonicalDocument) -> (ChunkDecision, 本次消耗 token)`。
- 上线即受 D-3 判例预算执法：`budget.resolve` 记账、超单文档 10% 上限则降级为 `RULE_DEFAULT_PROFILE` 且逐段 `decider="default"`、`human_review=True`，并计摄取池账（越界消耗不回滚成"未发生"）。

**历史文档的决议件迁移（ADR-010 破坏性变更的处置）**
- 旧形状 doc 级单值件（带 `chunk_type`/`boundaries`、无 `decisions`）在新模型下解析成**空 decisions → 重放零块**，属静默失真，不要靠"能解析"判断迁移已完成。
- 处置＝重放而非读兼容：整库走 `shadow_rebuild()`（逐 doc 从 objectstore 重拉正文重放，覆写新形状归档件），缺块追平走 `compensate()`（`origin=reconcile_backfill`）。
- 明确**不做**双形状兼容读：两套口径同时在系统里正是 ADR-010 要消除的病。

## 五、故障排查（现象→定位→处置）
- **表格被切成散文块**：`canonicalize._is_table` 采"宁可少判"（整块每行带 `|` 或制表符、第二行是表头分隔行，或首末行列数相等）。不满足就落 paragraph。
  规则档认不出的版面表＝第②级供给，不要在本档加启发式硬凑（误判成表＝把正文当原子块，检索不可分）。
- **代码块被空行拆开**：只有 ``` 围栏内空行才保结构；裸代码（无围栏）判不出 code，这是规则档边界，不是缺陷。
- **两个函数出现在同一个 code 块里**：不是"跨切"。D-2 对 code 的承诺是切口落在函数/类头，<100 token 的同型短节会被
  `merge_short` 并入前一节——块内 `def` 计数与源一致就没问题；要强制一函数一块，得把 `_MIN_TOKEN_MERGE` 对 code 型豁免（=改 D-2 口径，按上一节走三处同步）。
- **出处卡片把一张表压成一行散文**：右侧联动面板按 `SourceView.chunk_type` 分排（表格/代码用等宽保留换行）。字段缺失或恒为 `text`
  ＝`/api/source` 没把切块结果的块型透出来（回归锁在 `tests/contract/test_gateway_http.py::test_source_readback_exposes_d2_chunk_type`），
  前端不自行判型——判型口径唯一在 `chunker`。
- **`parent_chunk_id` 指向不存在的 chunk_id**：一定是有人把 `link_table_parents` 挪到了 `merge_short`/重编号**之前**。顺序约束见 `pipeline.py` 切块调用行注释。
- **审计出现 `provider_fail_loud` 且 detail 含 F5**：某块超 8192 token 被截断（病态超长单函数或无边界表单元）。这是**非静默**要求，不是故障；处置=按 D-2 收紧对应块型档位或补结构边界。
- **问答莫名拒答（confidence 偏低）**：先看该 doc 块数与块长（`chunker.plan` 结果或回执 `chunk_count`）——整篇塌成 1 块会压死 coverage。

## 六、本档不承诺
第②级版面/分类小模型（Word 直读 docx 原生 XML 结构）、第③级 LLM few-shot、四类语料（制度/合同/纪要/代码 Wiki）各 ≥100 例 profile 路由人工抽检、结构识别三层占比报表、真实 PDF 版面回捞质量——均属放行项⑧ 剩余半边/金标真机评测（R4/A2）。
