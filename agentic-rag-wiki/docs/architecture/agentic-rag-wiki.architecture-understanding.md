# 架构图与流程图怎么读（现档 2026-10-14，M4-cn 之后）

**目的**：给三类读者一份与代码逐点对齐的架构视图——①新人建立心智模型，②评审者核对"声称↔执法"，③运维定位动作落在哪个入口。
**产物**：`agentic-rag-wiki.structurizr.dsl`（C4 语义源，7 个视图）+ `flow-ingest.dot` / `flow-ask.dot`（两条链的流程）+ `agentic-rag-wiki.evidence.md`（逐节点证据）。
**用哪张答哪个问题**：

| 问题 | 视图 |
| --- | --- |
| 这系统是谁的、碰了哪些外部能力 | `c1-context` |
| 三分区 + 平台平面 + 数据存储怎么摆 | `c2-containers` |
| 跨区触达从哪进、谁能写索引 | `c3-gateway`（组合根唯一注入点）＋`c3-ingest`／`c3-wiki` |
| 熔断/契约/护栏各自在哪一层 | `c3-platform` |
| 一篇文档从上传到进索引经过哪些门 | `flow-ingest.dot` |
| 一次提问怎么走到答案、高负载时怎么降 | `flow-ask.dot` |

## 关键口径（图里画了、代码里也执法的）

1. **组合根是全仓唯一注入点**：其余跨区触达一律经端口。`c3-gateway` 里 `composition.py` 出边最多，是刻意的——别的模块看不到 provider 具体实现。
2. **分区是逻辑容器，不是进程**：`gateway / ingest / wiki / memory / platform` 都打了 `LogicalInProcess` 标签。执行档＝单进程 uvicorn，进程拆分属 ADR-005 与实机放行项，**不要按图以为有五个部署单元**。
3. **A 区是唯一的索引写入者**：`c3-wiki` 里 B 区只有一条出边指向 A 区 `IngestionPort`（admit/withdraw/is_indexed）。"B 区禁直写索引"（跨区三禁②）在图上是**不画那条边**——禁令本身由 `check_imports` 机检，别指望图能拦。
4. **`Unverified` 标签＝放行项，不是现状**：真实源系统（㉒）、企业档 PG/Milvus 双存储（②）、C 区自动写参数（三禁③，执行档无该通道）三处用虚线＋白底红框，读图时先当"不存在"。
5. **`BuildTime` 标签＝构建期**：`harness` 那 10 道门扫源码，不在请求路径上，和运行时熔断不是一回事。

## 假设与已知不精确处

- 容器视图把 dev 档实现（内存向量/文件对象库/进程内队列）与企业档档位（pgvector·Milvus/S3/Kafka/PG）写在同一节点的 technology 串里——**执行档跑的是前者**，后者是同一端口的可换档位。
- `flow-ask.dot` 的 B2 soft=6/hard=8 是**单机执行档**读数（`PoolBudget.for_hard(Settings.inflight_llm_limit)`）；企业档 50 等比放大到 peak=38/hard=50，同一纯函数无档位分支。
- 问答链的多跳（④层）与 LLM 生成在执行档默认走 dev echo 档，除非装配表选了 local/cloud——图上的 `LlmRouter` 节点表达的是**路由形状**，不是"一定有一次真云端调用"。
- 流程图按"一次请求经过的判定顺序"排，不是时序图：`fan-out → WikiLoop` 之间隔着一次队列消费，实际可跨进程/跨批次。

## 维护约定

- 改架构先改 `.dsl`，预览是派生物；图与代码漂移时**以代码为准并当轮修图**（同 §9 产物清单口径）。
- 新增可达入口须同轮补 `docs/runbooks/entry_matrix.md` 一行——该矩阵由 `runbook_check` 与 `gateway/http.py`·`gateway/cli.py` 的代码注册位 **1:1 比对**（多一条少一条重复一条皆红，M4-cl 已机检）；`flow-*.dot` 侧仍属**人工维护约定、无机检门**（图与代码漂移以代码为准并当轮修图，同上一条）。
- 本次三张图均由插件自带解析器实测通过：`.dsl` 经 Structurizr DSL parser 解析出 7 个视图无 error；两个 `.dot` 均渲染出 SVG。
