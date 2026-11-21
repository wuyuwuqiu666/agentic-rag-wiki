# Agentic RAG + LLM Wiki 企业知识库

一套「企业知识库系统」的完整工程蓝图 + 单机可跑实现骨架：**Agentic RAG**（多跳检索、引用溯源、拒答优于错答）+ **LLM Wiki**（LLM 持续把原始文档编译成人审发布的结构化知识页）。

> **一句话定位**：架构按企业档（10 万员工 / 百万文档）设计，**执行档 = 单机缩量**（5 万文档 / 1 千变更每日 / ≤5 并发 / RTX 5060 8GB 全程可跑）。承诺只写在能亲手跑通的那一档；企业档与所有依赖真实世界输入的能力均按「诚实账」标注在放行清单里。

## 核心特性

- **一切皆组件**：15 个端口协议（Embedding / Rerank / Llm / VectorStore / Lexical / ObjectStore / Mq / Cache / Ocr / FileParser / Graph / Ingestion / Guard / Audit / Inflight），云端与本地双实现并存，组合根唯一注入，换实现只改装配、业务代码零改动。
- **Agentic RAG**：ACL pre-filter → 向量 + BM25 双召回 → RRF(k=60) 融合 → cross-encoder rerank（≤50 候选）→ top-n 5–8 进 LLM；逐句引用校验、无据句拦截、低置信拒答、步数预算 ≤8。
- **LLM Wiki**：triage 入选判定 → 大纲/草稿 → 人审发布门（代码不变式，草稿不可直发）→ 高权威块回写索引（wiki 优先）；回写 ACL = 源块交集（封跨源泄露）。
- **分层熔断 B0–B5**：判定（零 IO 纯函数）与执行分离，规则 YAML as-code；v1 仅 B1（批量删除）/ B3（供给）/ B5（完整性）硬断，B2 软告警、B4 shadow；B0 总闸冻结写侧保读侧、双人复位、**重启不算复位**（append-only 审计回放）。
- **护栏即代码**：10 道 harness 机检门（import 方向 / 跨区三禁 / 契约向后兼容 / 熔断规则 / 参数双级白名单 / 生命周期 / 配置面 / 后端选择 / runbook / 依赖许可证），每条约束都有测试锁。
- **诚实账**：区分真实现与 dev/fake 档（默认 embedding/rerank/llm 为确定性哈希与模板回显，**不是质量基线**），22 项实机放行项逐条在册。

## 目录结构

```
.
├── PRD-agentic-rag-llm-wiki.md          # 产品需求定义 v0.7.1（待 B1 评审）
├── R8-ingest-performance-design.md      # 企业档摄取性能参考设计（归档）
├── local-model-selection.md             # 本地模型选型调研
├── bench/                               # P3 本机实测基准与脚本
└── agentic-rag-wiki/
    ├── AGENTS.md                        # 全局约束（唯一的全局记忆入口）
    ├── pyproject.toml / .env.example    # 依赖装配表与配置模板
    ├── src/
    │   ├── platform/                    # 平台平面：types/config/providers/breaker/gateway/harness
    │   └── partitions/                  # 三分区：ingest(A) / wiki(B) / memory(C)
    ├── docs/adr/                        # 12 份架构决策记录（权威）
    ├── docs/architecture/               # C4 架构图（Structurizr）与流程图
    ├── docs/runbooks/                   # 熔断与运维处置页
    ├── docs/audit/                      # 全生命周期审计台账（编号唯一源）
    ├── hooks/                           # pre-commit / pre-release
    └── tests/                           # smoke / contract / chaos / perf / eval（78 文件）
```

## 快速开始

```bash
cd agentic-rag-wiki
pip install -e ".[dev]"          # 核心依赖极简：pydantic 系列 + PyYAML；其余为可选 extra
python -m pytest src/platform/breaker tests -q   # 一条命令跑全环
```

默认 `Settings` 全为 dev 档（零依赖、零外呼即可跑通闭环）。切真实档只改 `.env` 的 `APP_PROVIDERS__*` 装配位；选云档缺凭证、或非默认档缺驱动一律 fail-loud，**绝不静默回落**。详见 `AGENTS.md §1/§5` 与 `.env.example`。

## 文档导航

| 想了解什么 | 读哪里 |
| --- | --- |
| 系统目标 / 需求 / 验收口径 / 里程碑 | `PRD-agentic-rag-llm-wiki.md` |
| 全局工程约束 / 诚实账 / 放行项清单 | `agentic-rag-wiki/AGENTS.md` |
| 为什么这么设计、推翻过什么 | `agentic-rag-wiki/docs/adr/` |
| 架构总览怎么读 | `agentic-rag-wiki/docs/architecture/agentic-rag-wiki.architecture-understanding.md` |
| 运维处置流程 | `agentic-rag-wiki/docs/runbooks/` |

## 状态

离线可决面（代码 / 判定内核 / 护栏 / 机检）全部落地，全环约 **695 passed / 2 skipped**、10 道 harness 门全绿。真模型效果、真机性能、双存储持久化、外部源连接器与跨人治理流等依赖真实世界输入的能力，见 `AGENTS.md §8` 的 22 项实机放行清单——本仓库**不伪造这些达标数字**。

## License

见仓库内声明。项目处于评审阶段（PRD 状态：待 B1 评审）。
