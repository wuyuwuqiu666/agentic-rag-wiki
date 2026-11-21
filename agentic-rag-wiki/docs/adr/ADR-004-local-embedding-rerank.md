# ADR-004：本地 Embedding / Rerank 选型

- 状态：采纳（2026-09-25）
- 决策：`EmbeddingPort` 本地主档=Qwen3-Embedding-0.6B fp16（32K 上下文、MRL 1024 维、Apache 2.0）；`RerankPort` 本地主档=bge-reranker-v2-m3。
- 依据：本机 RTX 5060 Laptop 8GB 实测（`../local-model-selection.md`）。淘汰 bge-large-zh（512 截断与 800 token 均值块冲突）、8B 档（超显存）、Conan（非商用许可）。
- 后果：Blackwell 须 CUDA 12.8+ torch（NFR-6）；`providers/embedding_local.py`/`rerank_local.py` 惰性加载、越界 fail-loud。
