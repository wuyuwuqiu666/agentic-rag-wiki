# ADR-001：断点续传不依赖编排框架

- 状态：采纳
- 背景：摄取管道需进程崩溃/抢占后可恢复，代价 ≤ 单片重跑。
- 决策：片状态机（pending→running→done/failed）落 PG，checkpoint 不绑定任何编排框架；GraphPort 仅声明 DAG，plain 档足够跑通 M0。
- 后果：换 langgraph 只换 `providers/orchestration_langgraph.py`（SDK 黑名单唯一豁免位），断点/幂等契约不变（`doc_id+version+chunk_seq`）。
- 实现状态（2026-10-02·M4-bt，经放行项⑮）：`orchestration_langgraph.py` 已由 fail-loud 占位升为真 LangGraph `StateGraph` 实现（`run`=线性节点图、`run_while`=step 圈+`tick` 计数+条件边，精确复刻 plain 首跳必执行/`not stop and passes<max_steps` 语义），`checkpointer` 可选注入 + `with_memory_checkpoint()`/`read_checkpoint()` 落内存检查点持久化；`tests/contract/test_graph_dual_backend.py` 跑 **plain↔langgraph 双实现同 `GraphPort` 契约套件**（逐字节同结果+同跳数+同执行序=证"换档只换实现、不动业务链"G5/E7）。依赖经 `pyproject [orchestration]` 可选 extra `langgraph>=1.2`（MIT）声明，**不进单机核心依赖**（默认 plain）。本决策口径不变；**残余仍待放行**=真·跨进程/PG 检查点持久化 + `interrupt` 人审真接线（现仍由 `wiki/service/review.py` 状态机离线代跑）。
