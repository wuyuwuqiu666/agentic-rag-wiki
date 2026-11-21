# ADR-012：触发台账从 append-only 审计账本**启动回放**——重启不再是双人复位的旁路

- 状态：采纳（2026-10-16，用户批复「继续完成 PRD 内容，如需要删除大量文件再申请，其余全部放行」；本条属全生命周期审计 #2 的收口刀 M4-cs）
- 关联：§6/B0 总闸"冻结写侧、保读侧、双人复位"、§10 风险表（复位旁路）、ADR-003（B0 覆盖范围）、ADR-011（M4-cp：判门前置于动作）、`src/platform/gateway/trip_ledger.py`（`counts_as_hard_trip` / `TripLedger.restore`）、`src/platform/gateway/policy_gate.py`（`check` / `restore_from_audit`）、`src/platform/gateway/composition.py`（建容器回放一次）、`src/platform/gateway/http.py`（`seed_demo` 返回状态 + `/api/health` 声明）、`tests/contract/test_trip_ledger_replay.py`、`docs/runbooks/breakers.md`。

## 背景
`TripLedger` 是**进程内**的：B0 的"连续未复位硬断达阈"完全靠内存里的几条触发记录。于是有一条谁都能走的后门——

1. **重启＝免签复位**。拉闸之后不必找第二个人签字，把进程一停一起，触发账清零、写侧立刻恢复，且审计里**一条 `breaker_reset` 都没有**。ADR-003 定的"双人复位"从此只是"进程不重启"的附条件承诺；混沌演练（M4-a）测的是同一进程内的累积与复位，看不见这条路径。
2. **口径本来就有两半**。写侧记账在 `PolicyGate.check`（判定"什么算一条真实硬断"），复位在 `reset_breaker`，而读侧判档在 `evaluate_b0`。"哪些审计事件代表真实硬断"这件事**没有单一判据**——修 restart 时如果各写各的，落账与回放的口径必然分叉（本项目反复踩过的坑：同一事实两种算法）。
3. **升级读数会自我喂养**。`ensure_write_allowed` 在每次被挡的写上都追加一条 `breaker_trip`（B0 升级读数，`action="block"`、**无 `shadow` 键**）。朴素地把"所有 `breaker_trip`"回放一遍＝冻结期越长、被挡的写越多、重启后读到的触发数越大——执法计数被自己的观测灌大，是"观测入口不得自己成故障源"的同一类缺陷（M4-ci #6 口径）。

## 决策
1. **耐久真相=已存在的审计账本，不新建存储**。执行档审计本来就是 append-only JSONL（`AuditSink`，`memory/service/audit_sink.py`），重启后仍在磁盘上。因此回放只读它：
   - **零新增文件、零新格式、零新表**；
   - **零 E3 契约变更**（`AuditEvent.kind` 九元 Literal 与 `extra: dict[str, Any]` 都够用）→ `contract_compat` 不需 `--update`；
   - **零新可达入口**（无新 HTTP 端点、无新 CLI 子命令）→ `docs/runbooks/entry_matrix.md` 行数不变、`runbook_check` 无需新增处置页。
2. **单一判据 `counts_as_hard_trip(event)`**：`kind=="breaker_trip"` ∧ `extra["shadow"] is False` ∧ `extra["action"]=="block"`。
   - `is False`（而非"键缺失即真"）＝**影子档观察**（`shadow=True`，开发默认 B1/B5 只记不拦）**和 B0 升级读数**（不带 `shadow` 键）都不算真实硬断——一条谓词同时解决"影子观察不该跨重启冻写侧"与"升级读数不回灌"；
   - 写侧记账与启动回放**调这同一个函数**（`PolicyGate.check` 用它决定要不要 `ledger.record`，`restore` 用它决定要不要重放），不再两处各判各的。
3. **`TripLedger.restore(events)`：按账本次序重放 trip / reset 对**，`reset` 消解该熔断**最近一条**未复位的触发；台账非空时**拒跑**（`ValueError`）＝回放只在组合根启动时执行一次，杜绝"跑两遍把同一触发计两次"。
4. **组合根在建容器时回放一次**（`guard.restore_from_audit()`，紧跟 `PolicyGate(...)` 之后）：**只读账本、不写审计、不改阈值**（跨区三禁③：账本记事实，判档仍只走 `ensure_write_allowed`→`evaluate_b0` 这一条路）。返回的条数只作诊断，调用方不据此判定。
5. **读不懂就拒起（fail-loud，NFR-8）**。账本行损坏→异常照上抛、容器不建成。**绝不**"读不到就当没有未复位硬断"——静默解冻正是本刀要消灭的行为。
6. **冻结期起门面不得带走读侧**（真·HTTP 跑出来的回归，见「后果」）。`seed_demo` 定义为**一次写侧动作**：捕获 `BreakerTripped` 后返回 `"skipped:<原因>"`，其它异常照旧上抛；`/api/health` 如实暴露 `seed` 字段。拉闸不炸启动、也不假装 seed 成功。

## 后果
- **正面**：
  - restart 不再是复位路径——三进程真 HTTP 演练：进程 1 三次危险下架达阈拉闸 → 进程 2（同审计目录、换进程）写侧仍 503 且读回的未复位触发数仍＝3（没清零＝没解冻，没变大＝升级读数未回灌）→ 独立进程 CLI 双人复位 → 进程 3 写侧恢复、随后一次危险下架照样被 **B1**（非 B0）拦下，执法未被削弱。
  - 复位同样跨重启生效，且 `breaker_reset` 留痕在账：复位从此只有"CLI 双人签字"一条路（HTTP 面故意不暴露复位，M4-x 口径）。
  - 只读不传感被两个方向锁死：重启与复位两个进程都没有新增 `breaker_trip` 观察；回放前后审计字节数不变。
  - 影子档（开发默认）零影响：仓内现档 `workspace/audit/*.jsonl` 历史硬断条数实测 **0**，起门面不会因本刀被冻。
- **代价与如实登记（不粉饰）**：
  1. **启动回放线性扫全账本**。当前量级无感；海量审计行下的启动耗时**未测**（属实机放行档，不填数字）。历史保留期仍由 `file_lifecycle.yaml` + `lifecycle_check`（M4-ah）界定，本刀不改。
  2. **判据依赖 `extra` 形状**。若将来新写一种"代表真实硬断"的事件而 `extra` 不带 `shadow=False`＋`action="block"`，它会**不被回放**（漏回放＝漏执法）。已用 `test_gate_writes_exactly_the_shape_replay_reads` 把"闸落账的形状＝回放读的形状"钉成契约测试：改落账形状必须同时改判据。
  3. **老环境可能带着未复位硬断上线**。升级本刀后，历史账本里真实未复位的硬断会被如实重放＝写侧起来就是冻住的。这是**正确行为**（欠的债不能靠升级抹掉），处置口径写进 `docs/runbooks/breakers.md`：按 runbook 核实后 CLI 双人复位。
- **契约兼容性**：不新增/不修改任何 pydantic 冻结模型与审计 kind；`counts_as_hard_trip` 是 `gateway` 层模块内函数，不进端口契约；可达入口集合不变。
