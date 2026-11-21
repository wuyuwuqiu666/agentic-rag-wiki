# 本地 Embedding / Rerank 模型选型（v1.0 已批准，2026-09-25）

> 定位：填 PRD v0.6.4 中 EmbeddingPort/RerankPort 的**本地实现**选型空白（对应 R8 §10 P3 端点实配登记、S2 供给档锚点、FR-ASK-5 rerank 候选模型）。
> 状态：**用户批复，P-1~P-5 已转正并入 PRD v0.6.4 = ADR-004（2026-09-25）**；本文件为 ADR-004 的调研附件/依据存档。
> **v0.7/v0.7.1 定位更新（2026-09-27，ADR-005/005.1）**：项目执行档缩至本机；供给档 v0.7 初判"云 API 主档 + 本地换插"，**v0.7.1 用户定档"embedding 模型用本地的够了"→ EmbeddingPort 主档=本地**。本文件的模型锚**全部继续有效**，现行角色：①**Qwen3-Embedding-0.6B=执行档 embedding 主档（常驻）**——在线单条 embed <0.5s、全量重灌 1M 向量≈26h 本机直算，**G1 承诺不再依赖任何云 TPM 配额**；②**bge-reranker-v2-m3=RerankPort 默认常驻档**（云 rerank 仅可选位）；③云 embedding 降为加速换插位；④LLM=云主档、本地 3B GGUF 按需换载（embed+rerank 常驻 ~3.2GB 后 8GB 已无 LLM 常驻位=显存调度策略非缺陷）；⑤"非产能单元/不进产能账"表述仅对**企业外推档**成立（R8 §3.1 归档口径）。P3 实测数据（稳态 10.6 chunk/s）=执行档 G1 承诺的计算依据。

## 0. 本机硬件实测（2026-09-25 检测）

| 项 | 实测值 | 选型含义 |
|---|---|---|
| GPU | NVIDIA RTX 5060 **Laptop**，显存 **8GB**（nvidia-smi 8151MiB），驱动 596.21 | fp16 模型权重 ≤4GB 才有余量给 batch+KV；更大数据集走量化 |
| 内存 | 16GB（16,162,598,912 B） | 模型+数据双加载下并发 ≤2 路 |
| CPU | AMD Ryzen 9 8945HX，16 核 | CPU 兜底档可跑 0.5B 级（慢 5–10 倍） |
| 架构 | Blackwell（sm_120） | **torch 需 CUDA 12.8+ 构建**；旧 wheel 不识别显卡——装环境第一步就查这个 |

## 1. Embedding 主选型对比（S2 本地档）

| 模型 | 参数/权重体积 | 上下文 | 输出维度 | 中文效果 | 8GB 卡上档位 | 许可 | 接入 |
|---|---|---|---|---|---|---|---|
| **Qwen3-Embedding-0.6B**（推荐·默认） | 0.6B / fp16≈1.2GB | 32K | 1024（MRL 200–1024 可调） | MTEB-zh 第一梯队（同家族 8B 版 72.55，0.6B 版≈bge-m3 级） | **fp16 直跑**，batch 余量大 | Apache 2.0 | sentence-transformers / vLLM / ollama / GGUF |
| BAAI/bge-m3（备选·对照） | 568M / fp16≈2.3GB | 8192 | 1024 | C-MTEB 久经验证 | fp16 可跑 | MIT | FlagEmbedding / sentence-transformers / ollama |
| Qwen3-Embedding-4B（升级位） | 4B / q4_k_m GGUF≈2.5GB | 32K | 2560（可调） | 更强 | **仅量化可跑**（GGUF via llama.cpp/ollama） | Apache 2.0 | ollama / llama.cpp |
| Qwen3-Embedding-8B | 8B / fp16≈16GB | 32K | 4096 | 强 | **本机跑不动**，属云端/大卡档 | Apache 2.0 | — |
| bge-large-zh-v1.5 | 326M / ≈1.3GB | **512** | 1024 | 中文好但短 | 可跑 | MIT | FlagEmbedding |
| gte-Qwen2-1.5B-instruct | 1.5B / ≈3.3GB | 8192 | 1536 | 强 | fp16 紧张，需量化 | apache 2.0 | sentence-transformers |

**淘汰理由**：bge-large-zh 512 token 与 FR-ING-7 的 800 token 均值 chunk 冲突（截断=静默丢信息，违反"可滞后不可背离"精神）；8B 档超显存；Conan-Embedding 非商用许可直接排除（企业场景）。

## 2. Rerank 选型（FR-ASK-5 cross-encoder 位）

| 模型 | 参数/体积 | 上下文 | 8GB 档 | 许可 | 备注 |
|---|---|---|---|---|---|
| **bge-reranker-v2-m3**（推荐·默认） | 568M / ≈2.3GB | 8192 | fp16 直跑 | MIT | FR-ASK-5 写的"bge-reranker-v2-m3 级"即此；候选≤50 条×top-k 延迟 ~100–150ms 在本卡可达 |
| Qwen3-Reranker-0.6B（对照位） | 0.6B / ≈1.2GB | 32K | fp16 直跑 | Apache 2.0 | 与 embedding 同家族，可作 M4 后换装评估项 |
| bge-reranker-large | 560M / ≈2.2GB | **512** | 可跑 | MIT | 512 截断问题同上，不选 |

## 3. "切块用的向量"选型（FR-ING-7 结构识别/语义断点打分）

切块过程中的向量有两种用途，分开答：

1. **索引向量**（chunk 落库供检索）：就用 §1 的 Qwen3-Embedding-0.6B，`Chunk.embedding_model` 字段登记为 `qwen3-embedding-0.6b`，`input_token_limit=32768`（天然覆盖 800 token 均值 chunk，无截断风险）。
2. **切块决策向量**（语义断点打分 / late chunking 实验位）：**建议与索引模型同源同版本**。理由：断点打分本质是"相邻句对的相似度"，用不同模型会引入一套独立的向量版本管理，违反"一切皆组件但要成对可重建"的 B5 对账口径。若跑量太大想省显存，可降级用 `bge-small-zh-v1.5`（24M，<0.2GB）做**纯离线实验对照**，不进主车道。
3. **注册表联动**：切块 profile 配置键 → 模型注册表的映射里，`embedding_model` 变更 = 全量重建事件（派生索引可滞后不可背离），这条已在 FR-ING-7 契约内，选型不新造规则。

## 4. 供给账对照（ vs R8 §3 S2 档）

- R8 S2 锚点写的"0.5–1B 级模型、≥150 chunk/s/推理单元"——本机 5060 Laptop 跑 0.6B fp16，**P3 实测已回填（2026-09-26，见 bench/p3-results.md）：800 token 块 batch=32 安全档=突发 14.7 chunk/s、150s 稳态 10.6（最低 9.7）chunk/s**。本文件初稿写的"参考 25–60 chunk/s"是同类硬件经验估算，**已被实测下修**（估算未计入 Windows WDDM 显存超订惩罚与稳态功耗墙）。
- **实测判语：本机 = dev/smoke + S2 功能验证档，是 S2 锚点的 0.07×，不是产能单元。** 72h/96h 承诺不靠这台机器背；产能按 R8 口径 = 服务器级 ≥24GB 卡（batch 不被显存封顶）或云 API。此机跑满也只是 S3 以下。
- P3 实配登记表新增一行：`本机 5060L/8G + qwen3-embedding-0.6b fp16 + batch 64 → 实测 chunk/s = ___`（留白，实测回填）。

## 5. 接入方式建议

- **dev 首选**：sentence-transformers（CUDA 12.8+ torch nightly/官方 cu128 wheel）——走 EmbeddingPort 本地实现，进程内调用，延迟最低。
- **ollama 备选**：`ollama pull qwen3-embedding:0.6b`——服务化解耦，但 embedding 走 HTTP 每批多 5–10ms，且 8GB 显存要与别的模型抢；仅演示/异构语言栈时用。
- 两者都包在 EmbeddingPort 后面，正好验证 ADR-001"一切可替换"：切实现不改业务代码。

## 6. 提案清单

> **更新 2026-09-25**：切块决策链三条提案（D-1 决议记录契约 / D-2 块型默认档位表 / D-3 判例预算 10% 上限）已经用户批复并入 **PRD v0.6.3**；下表 P-1~P-5 亦经批复并入 **PRD v0.6.4 = ADR-004**，本表保留作落点索引。
> **更新 2026-09-26**：P-4 已兑现——R8 P3 留白行以实测回填（`bench/p3-results.md`，稳态 10.6 chunk/s=锚点 0.07×）。§4 的吞吐估算据此下修。D-4/D-5/D-6 三条提案经用户裁决（企业级不走笔记本堆卡，按推理单元结算）**已并入 PRD v0.6.5**（NFR-8 fail-loud / R8 §3.1 单元规格与数量=2 / NFR-7 稳态口径）。

| # | 动作 | 落点 |
|---|---|---|
| P-1 | EmbeddingPort 本地默认实现 = Qwen3-Embedding-0.6B（fp16），备选对照 = bge-m3 | PRD §8.x FR-ING-7 附注 + 新 ADR-004 |
| P-2 | RerankPort 本地默认 = bge-reranker-v2-m3，对照位 Qwen3-Reranker-0.6B | FR-ASK-5 附注 + ADR-004 |
| P-3 | 切块决策向量与索引向量同源同版本；降级对照模型不进主车道 | FR-ING-7 |
| P-4 | R8 P3 表补"本机 5060L"实测行（数值留白待测）+ 本机定位=dev/smoke 非产能 | R8 §10 P3、§3 S 档注 |
| P-5 | 环境红线：Blackwell 需 CUDA 12.8+ 构建的 torch，写入 NFR/开发环境文档 | M0 落地时生效 |

Sources:
- [中文 RAG 系统 Embedding 模型选型技术文档（GitHub ForceInjection）](https://github.com/ForceInjection/AI-fundamentals/blob/main/07_rag_and_tools/rag_basics/chinese_rag_embedding_model_selection.md)
- [Qwen3-Embedding 官方仓库 README](https://github.com/QwenLM/Qwen3-Embedding)
- [BGE-M3 vs Qwen3-Embedding 实测对比（CSDN）](https://blog.csdn.net/vulkan6gpu/article/details/152545992)
- [BAAI/bge-m3 vs Qwen3 Embedding 0.6B 对比](https://agentset.ai/embeddings/compare/baaibge-m3-vs-qwen3-embedding-06b)
- [Best Ollama Embedding Models 2026](https://www.morphllm.com/ollama-embedding-models)
- [Best Ollama Models for 8GB VRAM](https://localaimaster.com/blog/ollama-model-ram-vram-table)
