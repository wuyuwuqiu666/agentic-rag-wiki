"""A 区摄取运行时：canonical→决议→切块→准入→embed→双写→fan-out；并实现 IngestionPort（B→A 回写唯一入口）。

一切依赖由组合根注入（端口），本模块零 SDK、零跨区 import（check_imports 执法）。
NFR-8：embed 按安全 batch 分批发，越界即 fail-loud（不静默降速）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from src.partitions.ingest.repo.acl_audience_ledger import AclAudienceLedger
from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.repo.delete_ledger import RETAIN_SECONDS_30D, DeleteLedger, Tombstone
from src.partitions.ingest.repo.source import SourcePort
from src.partitions.ingest.service import admission, budget, canonicalize, chunker, intake, reconcile, reindex
from src.platform.providers import (
    AuditPort,
    CachePort,
    EmbeddingPort,
    GuardPort,
    LexicalPort,
    MqPort,
    ObjectStorePort,
    OcrPort,
    VectorStorePort,
)
from src.platform.providers.errors import ProviderError
from src.platform.types import AuditEvent, CanonicalEvent, ChangeEvent, Chunk, Document

FANOUT_TOPIC = "canonical.fanout"


class ChangeApplyError(RuntimeError):
    """一批变更里**有逐条失败**：异常照旧上抛（fail-loud，绝不吞），但**逐条账随异常一起带出来**（M4-cr／审计 #17 补录）。

    旧行为＝批内第 k 条抛错，前 k-1 条的效果已经落地（索引已清、清单已改账、墓碑已记），而调用方只拿到
    一句异常文本、整批回执为零＝"部分效果无账"——本档最忌的形态之一（与 M4-cj"503 且零部分效果"、
    M4-cp"冻结期不消耗待办"同族）。带上逐条账不改变"这是一次失败"，只改变"失败之后能不能对账"。
    """

    def __init__(self, message: str, *, receipt: dict[str, Any]) -> None:
        super().__init__(message)
        self.receipt = receipt


class IngestPipeline:
    def __init__(
        self,
        embedding: EmbeddingPort,
        vectorstore: VectorStorePort,
        objectstore: ObjectStorePort,
        mq: MqPort,
        batch_safe: int = 32,
        guard: GuardPort | None = None,
        audit: AuditPort | None = None,
        inventory: CanonicalInventory | None = None,
        lexical: LexicalPort | None = None,
        cache: CachePort | None = None,
        ocr: OcrPort | None = None,
        ocr_min_chars: int = 1,
        reconcile_dir: Path | None = None,
        delete_ledger: DeleteLedger | None = None,
        delete_retain_seconds: float = RETAIN_SECONDS_30D,
        acl_ledger: AclAudienceLedger | None = None,
        precedent_ledger: budget.PrecedentLedger | None = None,
        chunk_fallback: budget.LlmFallbackDecider | None = None,
    ) -> None:
        self._embedding = embedding
        self._vectors = vectorstore
        self._objects = objectstore
        self._mq = mq
        self._batch = batch_safe
        self._guard = guard
        self._audit = audit
        self._inventory = inventory or CanonicalInventory()
        self._lexical = lexical
        self._cache = cache
        self._ocr = ocr
        self._ocr_min_chars = ocr_min_chars
        self._reconcile_dir = reconcile_dir
        # §10 软删除回滚点：删除落地即记可恢复墓碑（正文留 objectstore），供 tombstone 恢复作业；缺省自建。
        self._delete_ledger = delete_ledger or DeleteLedger(retain_seconds=delete_retain_seconds)
        # 受众前值账（M4-cq）：acl_change 的最新域与改前域在此留痕，三路重放（恢复/补偿/重写）共用同一口径；缺省自建。
        self._audiences = acl_ledger or AclAudienceLedger()
        # D-3 判例预算：摄取池账本管线持有（跨文档累计、按 doc_id 分账）；③级 LLM decider=组合根注入，执行档缺省不冒充。
        self._precedent = precedent_ledger or budget.PrecedentLedger()
        self._chunk_fallback = chunk_fallback

    def process_document(
        self, document: Document, *, origin: Literal["ingest", "reconcile_backfill"] = "ingest"
    ) -> CanonicalEvent:
        if self._guard is not None:
            self._guard.ensure_write_allowed("ingest")  # B0 总闸：达阈冻结写侧，读侧不受影响
        work = self._resolve_scan(document)  # FR-ING-8 三态分流：scanned/mixed 走 OCR 旁路（第⓪刀），digital 主车道
        if work is None:  # OCR 质量门未过=失败页，不进索引、不 fan-out（审计已隔离）
            return CanonicalEvent(
                doc_id=document.doc_id,
                version=document.version,
                domain=document.domain,
                acl_tags=document.acl_tags,
                doc_type=document.doc_type,
            )
        canonical, event = canonicalize.canonicalize(work, self._objects, origin=origin)
        outcome = budget.resolve(canonical, ledger=self._precedent, decider=self._chunk_fallback)
        decision = outcome.decision
        if outcome.human_review and self._audit is not None:
            # D-3：判例越 10% 上限→该文档判例作废、回落规则默认 profile；B2 软告警口径上报并标记人审，不静默也不阻断
            self._audit.emit(
                AuditEvent(
                    kind="breaker_trip",
                    partition="ingest",
                    doc_id=canonical.doc_id,
                    detail=f"{outcome.reason}；判例降级为 {decision.profile}（decider=default），待人审",
                    extra={"breaker": "B2", "action": "alarm", "human_review": True, "pool": self._precedent.pool},
                )
            )
        # D-1：决议记录随 doc_version 归档 objectstore（键 {doc_id}/{version}#decision），切块=对决议确定性重放，可审计可回放
        self._objects.put(f"{canonical.doc_id}/{canonical.version}#decision", decision.model_dump_json().encode("utf-8"))
        # D-2：短节并入父节；表格行组父子链必须在重编号之后回填（chunk_id 按序号派生）
        chunks = chunker.link_table_parents(chunker.merge_short(chunker.apply(canonical, decision)))
        result = admission.admit(chunks)
        if result.quarantined and self._audit is not None:
            for c in result.quarantined:
                self._audit.emit(
                    AuditEvent(
                        kind="quarantine", partition="ingest", doc_id=c.doc_id,
                        detail=f"块 {c.chunk_id} 未过 ACL/DLP 准入", extra=admission.dlp.ruleset_tag(),
                    )
                )
        f5_truncated = chunker.f5_gate(result.accepted)  # F5：超下游 input 上界截断，须告警入审计不得静默
        if f5_truncated and self._audit is not None:
            for cid in f5_truncated:
                self._audit.emit(
                    AuditEvent(
                        kind="provider_fail_loud",
                        partition="ingest",
                        doc_id=cid.split("#", 1)[0].rsplit("/", 1)[0],
                        detail=f"块 {cid} 超下游模型 input 上界→按 F5 截断（非静默，NFR-8 fail-loud 口径）",
                    )
                )
        if self._guard is not None and chunks:
            # B4 注入/外泄探测（v1 shadow=只记不拦，M4 评估硬断）：隔离占比作注入压力信号
            self._guard.check("injection_score", len(result.quarantined) / len(chunks), "B4", partition="ingest")
        self._embed_and_index(result.accepted)
        # 新正文入索引＝同问句的旧答案从此过时：不清则"文档已可检索、答案仍答旧内容"的不可见窗口＝缓存存活期（进程寿命）。
        # 口径与 `admit` 的回写路对称（按块 ACL 域逐个清，CachePort 键首段恒为域指纹）；差别是源块不限 authority。
        if result.accepted and self._cache is not None:
            for tag in {t for c in result.accepted for t in c.acl_tags}:
                self._cache.invalidate_domain(tag)
        self._inventory.record(event, [c.chunk_id for c in result.accepted if c.chunk_id])
        self._mq.publish(FANOUT_TOPIC, event.model_dump())
        return event

    def _resolve_scan(self, document: Document) -> Document | None:
        """FR-ING-8 第⓪刀：按 page_state 分流。digital 原样进主车道；scanned/mixed 经 OcrPort 取正文过质量门。

        返回 None=OCR 空/退化（失败页）→ 调用方跳过索引与 fan-out；未装配 OCR 供给即 fail-loud（绝不把扫描件当 digital 入库）。

        门禁↔供给分工（M4-cf 放行项⑦ 收口后）：**识别**发生在解析边界（`FileParserPort` 内的 `ScanOcrPort`，
        唯一摸过页字节处，产出 raw_text + page_state + 失败页账），本刀只做**准入**（对已识别正文过质量门）。
        故 dev 档 `PassthroughOcr` 在扫描件的正常路由里是"取解析已给的正文"而不是空转冒充 OCR——同一页不认第二遍
        （两次识别＝同一份文档两种字，本项目反复拦的口径分叉族）。
        """
        if not intake.needs_ocr(document.page_state):
            return document
        if self._ocr is None:
            raise ProviderError(
                f"{document.doc_id} page_state={document.page_state} 需 OCR 旁路但未装配 OcrPort（FR-ING-8 fail-loud，禁当 digital 静默入库）"
            )
        recognized = self._ocr.recognize(document)
        if not intake.ocr_output_ok(recognized, self._ocr_min_chars):
            if self._audit is not None:
                self._audit.emit(
                    AuditEvent(kind="quarantine", partition="ingest", doc_id=document.doc_id, detail=f"OCR 质量门未过（page_state={document.page_state}）→失败页不进索引")
                )
            return None
        return document.model_copy(update={"raw_text": recognized})

    def reconcile(self, *, sense: bool = True) -> dict[str, int]:
        """B5 对账：清单应索引数 vs 索引实际数，背离债交熔断闸（M1 shadow 观察、M4 硬断）。

        应索引态含两册（源块＋已发布页的回写块，见 `CanonicalInventory` 分册理由）：回写块缺册时被发布过的
        每一块都以"索引有、清单无"的孤儿形态虚报，真背离（索引掉块）反被固定噪声淹没——`extra` 从此只报真孤儿。
        `writeback`=在册回写块数：运维要能看出这一轮账里有几块属权威页（其缺块的处置＝B 区重编译，非重拉正文）。

        `sense=False`＝**只读不传感**：同一套数（同一计算路径，不分叉口径），但不向熔断闸记这一次观察。
        理由不是洁癖是运维安全——读回入口（`GET /api/ingest/reconcile`、CLI `admin reconcile`）会被反复刷新，
        而 B5 硬断档下每次越阈都记一条未复位触发，连续达阈即升 B0 冻结全部写侧；"看一眼账"不得变成"自己把系统拉闸"。
        传感仍留在写侧动作的既有调用点（如恢复后自跑对账）与逐窗采样作业（真实调度=放行档）。
        """
        expected = self._inventory.expected_ids()
        actual = set(self._vectors.all_ids())
        missing = len(expected - actual)
        extra = len(actual - expected)  # 孤儿块（索引有、两册清单皆无）
        if sense and self._guard is not None:
            self._guard.check("reconcile_missing_chunks", float(missing), "B5", partition="ingest")
        return {
            "expected": len(expected),
            "actual": len(actual),
            "missing": missing,
            "extra": extra,
            "writeback": len(self._inventory.writeback_ids()),
        }

    def compensate(self, *, now: float | None = None, write_report: bool = True) -> dict[str, Any]:
        """FR-ING-4：对账→落 `workspace/reconcile/` 报告→"一键补偿"=按缺失块反查受影响 doc、用清单指针重拉 objectstore 正文重放入库。

        缺失块所在 doc 整体重放（upsert 幂等修复该 doc 全部块，origin=reconcile_backfill 标注非新内容）；
        正文不可拉（真孤儿/无指针）=不臆造内容、留 B5 背离债；孤儿块只报告不自动删（删除须过 B1、交变更流处置）。

        回写块缺账（`writeback` 册里的 id）**不经本作业补**：A 区没有页正文，凭 canonical 重放只会造出源块、
        造不出那张权威页——补它=臆造内容。处置在 B 区（重编译→人审→重发布），本刀把它从"待补偿"里剥出来
        并按页归属如实写进报告（`missing_writeback`），背离债仍按两册合计计（不降口径）。

        **B5 传感前置于写侧之前**（M4-cj，与 M4-cb 事务口径"判门在前→写侧→改状态"同一条纪律）：补偿是批量重放，
        若先写完再判，硬断档下（`reconcile_missing_chunks` ≥100）会抛 `BreakerTripped` 让调用方收到 503，
        而此刻索引已被改、报告未落、回执没给出——正是本档最忌的"部分效果无账"态（对照 M4-ce 1a/1b 的"503 且零部分效果"）。
        前置后触发即零写侧效果。**一次动作只传感这一次**：修后复核走 `reconcile(sense=False)`，同一路径取数、不重复记观察。

        **B0 总闸同样前置**（M4-cv／审计 #14）：此前本函数只有 B5 那一道 `check`，而 B0 的门住在 `process_document` 里——
        于是"冻结期点补偿会怎样"取决于**这一批里有没有一条正文真能重放**，而不是取决于"补偿是写侧动作"这条规则：
        `missing=0`、或缺的全是"正文不在册、补不了"那一份时，循环一次也没碰到门，拉闸期照样落报告、返回**成功形状回执**
        （200＋`backfilled:0`），运维读成"账实相符、不必处理"。前置后整批在第一步就被问、被拒（503＋点名总闸），
        且一次动作只被问这一次（不在循环里逐 doc 判门）。
        """
        if self._guard is not None:
            self._guard.ensure_write_allowed("ingest")  # B0 总闸：补偿是写侧动作，冻结期须先被问（审计 #14）
        expected = self._inventory.expected_ids()
        actual = set(self._vectors.all_ids())
        all_missing, extra = reconcile.diff(expected, actual)
        writeback = self._inventory.writeback_ids()
        wb_missing = {
            page_id: [cid for cid in ids if cid in set(all_missing)]
            for page_id, ids in self._inventory.writeback_pages().items()
        }
        wb_missing = {p: ids for p, ids in wb_missing.items() if ids}
        missing = [cid for cid in all_missing if cid not in writeback]  # 只这份才是"可重拉正文"的
        ts = now if now is not None else time.time()
        if self._guard is not None:
            self._guard.check("reconcile_missing_chunks", float(len(all_missing)), "B5", partition="ingest")
        backfilled = 0
        for ev in self._inventory.docs_with_missing(set(missing)):
            if self._reindex_event(ev):
                backfilled += 1
        report_path: str | None = None
        if write_report and self._reconcile_dir is not None:
            self._reconcile_dir.mkdir(parents=True, exist_ok=True)
            report = reconcile.render_report(
                expected_count=len(expected),
                actual_count=len(actual),
                missing=missing,
                extra=extra,
                generated_at=ts,
                writeback_missing=wb_missing,
            )
            written = self._reconcile_dir / f"reconcile-{int(ts)}.md"
            written.write_text(report, encoding="utf-8")
            report_path = str(written)
        after = self.reconcile(sense=False)  # 修后复核＝同一计算路径的另一半账，不另立口径、不再传感
        return {
            "expected": len(expected),
            "actual": len(actual),
            "missing": len(all_missing),  # 背离债合计（两册都算）
            "missing_source": len(missing),
            "missing_writeback": sum(len(ids) for ids in wb_missing.values()),
            "extra": len(extra),
            "backfilled": backfilled,
            "writeback": len(writeback),
            "missing_after": after["missing"],  # 补完仍剩的债＝真孤儿/不臆造那部分，如实留账
            "report": report_path,
        }

    def _reindex_event(self, ev: CanonicalEvent) -> bool:
        """按清单/墓碑指针从 objectstore 重拉正文重放入库（复现同 chunk_id，upsert 幂等；补偿、恢复与 ACL 重写共用）。

        正文不可拉（无指针或 store_key 不存在=真孤儿/真丢）→ 返回 False，不臆造内容。
        **受众只问前值账的当前值**（M4-cq）：本方法是恢复、补偿、ACL 重写三路共用的唯一重放口，
        收紧一经入账就不会被任何一路重放撤销（否则"下架后收紧、恢复时回落旧受众"＝权限被动作悄悄取消）。
        origin=reconcile_backfill=「正文逐字节未变的重放」而非源出新内容（补偿/恢复/ACL 重写同属此语义；
        `origin` 是 E3 冻结二值 Literal，无"权限变更"第三枚，挪用专枚须人工 ADR——区分三种触发靠调用侧回执计账，不靠此字段）。
        """
        if not self._has_body(ev):
            return False
        latest_acl = self._audiences.current(ev.doc_id, ev.version)
        doc = Document(
            doc_id=ev.doc_id,
            version=ev.version,
            domain=ev.domain,
            acl_tags=latest_acl if latest_acl is not None else list(ev.acl_tags),
            doc_type=ev.doc_type,
            raw_text=self._objects.get(ev.store_key).decode("utf-8"),
        )
        self.process_document(doc, origin="reconcile_backfill")
        return True

    def acl_history(self, doc_id: str, version: int | None = None) -> list[dict[str, Any]]:
        """受众变更前值账的读回面（只读、不传感、不写侧）：逐条给「哪一版、从哪些域、收到哪些域、落在哪」。

        `applied_to` 三档＝`inventory`（在册块两路已改写）/`retired`（文档已下架，改的是账：恢复即按新域供答）/
        `pending_body`（正文指针缺失，索引未改：补偿重放时按新域）。跨进程可读与真权限行=放行项②。
        """
        return [
            {
                "doc_id": e.doc_id,
                "version": e.version,
                "before": list(e.before),
                "after": list(e.after),
                "ts": e.ts,
                "applied_to": e.applied_to,
            }
            for e in self._audiences.history(doc_id, version)
        ]

    def pending_deletes(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """§10 删除墓碑选单（下架可恢复账的可读面）：逐条给「能不能恢复、何时过期」。

        只列**仍未恢复**的墓碑（已回滚的不再是待办）：保留期内 `recoverable=True`，过窗=False 并如实报
        `expired`（超出可回滚窗口不臆造"还能一键找回"）。正文指针（store_key）缺失者虽在账上，恢复必失败，
        故一并回 `has_body`，让调用侧在点恢复前就知道这条找不回来。
        """
        ts = now if now is not None else time.time()
        rows: list[dict[str, Any]] = []
        for t in self._delete_ledger.within_retention(ts):
            rows.append(self._tombstone_view(t, ts, recoverable=True))
        for t in self._delete_ledger.expired(ts):
            rows.append(self._tombstone_view(t, ts, recoverable=False))
        return rows

    def _has_body(self, ev: CanonicalEvent) -> bool:
        """正文是否真在 objectstore（恢复可行性的唯一判据；与 `_tombstone_view` 同一口径，不分叉）。"""
        return bool(ev.store_key) and self._objects.exists(ev.store_key)

    def _tombstone_view(self, t: Tombstone, ts: float, *, recoverable: bool) -> dict[str, Any]:
        ev = t.event
        return {
            "doc_id": ev.doc_id,
            "version": ev.version,
            "domain": ev.domain,
            "chunk_ids": list(t.chunk_ids),
            "deleted_at": t.deleted_at,
            "retain_until": t.retain_until,
            "seconds_left": max(0.0, t.retain_until - ts),
            "recoverable": recoverable,
            "has_body": self._has_body(ev),
        }

    def restore_deletes(self, *, doc_id: str | None = None, now: float | None = None) -> dict[str, int]:
        """§10「tombstone 恢复作业」：保留期内被删文档从 objectstore 重拉正文回滚重放（误删一键恢复）。

        只回滚「未过保留期且正文仍在」的墓碑（确定性重放同 chunk_id→upsert 幂等复现，非新内容）；
        过窗=超出可回滚窗口、正文真丢=不臆造——二者都恢复不了，但分账记：过窗进 `expired_unrecoverable`、
        窗内正文已丢进 `body_missing`（前者是时间窗、要接受找不回；后者是账实丢件、要查 objectstore）。
        `recoverable` 因此只算「窗内且正文在」的那一份＝真可回滚面，绝不把找不回的记成"还能一键找回"。
        恢复后交 B5 对账复核账实（**本动作只传感这一次**：内部读数走 `reconcile(sense=False)`，M4-cn⑦）。
        `doc_id` 给定=只回滚该文档（UI 逐条恢复用），缺省 None=全量待恢复墓碑，与既有调用口径一致。
        """
        ts = now if now is not None else time.time()
        within = [t for t in self._delete_ledger.within_retention(ts) if doc_id is None or t.event.doc_id == doc_id]
        # 正文态在恢复前后不变（恢复只增不删），先一次性取快照再分账，避免重放途中数口径漂移。
        bodies = {id(t): self._has_body(t.event) for t in within}
        restored = 0
        for t in within:
            if self._reindex_event(t.event):
                self._delete_ledger.mark_restored(t.event.doc_id, t.event.version)
                restored += 1
        if self._guard is not None:
            rec = self.reconcile(sense=False)  # M4-cn #7：本方法末尾自己 check，读数不得二次传感（一次动作只传感一次）
            self._guard.check("reconcile_missing_chunks", float(rec["missing"]), "B5", partition="ingest")
        return {
            "restored": restored,
            # #8：recoverable 只算「窗内且正文真在」——正文已丢的墓碑窗内也找不回来，计入窗内数=虚报可回滚面
            "recoverable": len([t for t in within if bodies[id(t)]]),
            "body_missing": len([t for t in within if not bodies[id(t)]]),
            "expired_unrecoverable": len(
                [t for t in self._delete_ledger.expired(ts) if doc_id is None or t.event.doc_id == doc_id]
            ),
        }

    def shadow_rebuild(
        self, *, active_alias: str = "search_active", shadow_alias: str = "search_shadow"
    ) -> reindex.ReindexOutcome:
        """FR-ING-3 版本化索引重建：影子全量重放 → 别名原子切换 → 切换失败自动回滚旧别名。

        build_shadow=按清单逐 doc 从 objectstore 重拉正文重放（dev 档 upsert 幂等复现全量块）；
        切换/回滚的原子语义由 `run_shadow_reindex` 兑现，跨实现（Milvus 原生 collection alias）换插不减。
        注：dev 档单 store 不做 collection 物理隔离（重放写同一逻辑集）——"切换期错误率 <0.1%" 的数值
        验收须真实 PG+Milvus 双 collection（放行项⑪；双存储与真别名本体＝放行项②）；此处锁 build-先于-flip 与失败回滚两条控制不变式。

        **B0 写侧总闸前置于全量重放之前**（M4-cm）：本作业逐 doc 重放正文进两路索引＝最重的一次写侧动作，
        拉闸期若仍可执行，就等于"冻结全部写侧、保读侧"这条口径被最大的那条写路径绕过（对照
        `process_document`/`apply_changes`/`admit` 同一道门）。判门在前⇒冻结时零部分效果，不会先建完影子再报错。
        """
        if self._guard is not None:
            self._guard.ensure_write_allowed("ingest")

        def build_shadow() -> None:
            for doc_id, version in self._inventory.docs():
                for ev, _ids in self._inventory.affected(doc_id, version):
                    self._reindex_event(ev)

        return reindex.run_shadow_reindex(
            self._vectors, active_alias=active_alias, shadow_alias=shadow_alias, build_shadow=build_shadow
        )

    def revert_alias(self, *, from_alias: str, to_alias: str) -> None:
        """§10 高危回滚点「切回旧别名脚本，一键」的手动稳态入口（restore.md 指向此）：搜索别名整域退回 72h 保留的上一版本。

        与 `shadow_rebuild` 的自动回滚互补——那是切换失败即时回滚，此为切换成功后、事后人工判定新索引劣化
        （如错误率/相关性回退）主动整域退回旧 collection。同别名即拒；store 抛错原样上抛（fail-loud，绝不静默吞掉
        索引回退失败）。真实 collection 别名与操作审计随 PG+Milvus 双存储落地（放行项②）——dev 单 store 别名不影响
        查询面（search 直取逻辑集），此处只兑现「一键切回」的运维入口与非静默语义，不伪造企业级隔离。

        同一条 B0 纪律（M4-cm）：切别名＝改写侧搜索指向，判门先于触库；同名校验在门之后（先问"现在准不准写"，
        再问"这次写得对不对"），冻结期不得经回滚通道动索引。
        """
        if self._guard is not None:
            self._guard.ensure_write_allowed("ingest")
        if from_alias == to_alias:
            raise ValueError("from_alias 与 to_alias 不得同名（无法表达切回）")
        self._vectors.alias_swap(from_alias, to_alias)


    def is_indexed(self, doc_id: str) -> bool:
        """账实查询（M4-ce 1b 目录批量档的裁决依据）：该 doc_id 当前是否仍有登记在册的块。

        指纹位点账只是"少解析一遍"的加速，**索引清单才是真相**：经 `/api/ingest/change` 人工下架后清单账被清
        （`forget_doc`/`forget_version`），源里仍在的文件就应当重新入库——与 FR-ING-4 补偿同口径（源为真相），
        所以"这份要不要进"只问本方法，不问位点。
        """
        return bool(self._inventory.chunk_ids_for(doc_id))

    def pull(self, source: SourcePort) -> dict[str, int]:
        """源连接器批量入口（U5「不触碰 service/runtime」的消费端落点，FR-ING-1/2）。

        先全量存量逐份 process_document 入库，再取增量变更交 apply_changes（B0/B1 门在
        process_document/apply_changes 内已设，此处不旁路）。连接器只实现 `repo.source.SourcePort`、
        只产 types 契约对象；真实 OA/网盘/Git/SharePoint 读取端=放行项㉒，本 seam 用内存档
        `DevSource` 即可离线演练"契约→管线"这条消费链，不冒充真实调度。
        """
        ingested = 0
        for document in source.list_documents():
            self.process_document(document)
            ingested += 1
        applied = self.apply_changes(source.poll_changes())
        return {"ingested": ingested, **{f"change_{k}": v for k, v in applied.items()}}

    def apply_changes(self, changes: list[ChangeEvent], *, now: float | None = None) -> dict[str, int]:
        """变更批入口：先过 B0 总闸写侧预检，删除占比再过 B1 熔断闸（M1 shadow=只记不拦），再落地 upsert/delete/acl_change。

        delete 走 `_purge`=向量+词法两路同清并按域清缓存：只摘向量路，BM25 仍会召回已删块并写进引用（双索引同生同灭）。
        acl_change 走 `_acl_rewrite`=两路索引与清单按新域重写，再按前后域并集清缓存（效应②③，④随 fan-out）。
        每一条 acl_change 逐版落**受众前值账**（M4-cq）：最新域从此由账定，恢复/补偿重放不再回落旧受众；
        落点分三档如实回执——`acl_rewritten`（在册块已改）/`acl_pending`（正文缺失待补）/`acl_retired`（文档已下架，改的是账）。

        **逐条记账（M4-cr／审计 #17 补录）**：批里每一条变更都有一行结果（`applied`／`blocked`／`failed`）＋一句可指的原因，
        汇总为回执里的 `applied`／`blocked`／`failed` 三条计数。三条口径：
        ①**一条失败不吞掉整批的账**——逐条隔离、其余照跑，末尾上抛 `ChangeApplyError` 并把逐条明细挂在 `receipt["items"]`
        ＝既 fail-loud 又可对账（旧行为＝第 k 条抛错时前 k-1 条的效果已落地却查无一笔，"部分效果无账"）；
        ②**正文不在册／正文为空＝`blocked`，不是"重放成功"**——旧代码把 `b""` 当新版本重放＝把"内容清零"伪装成"更新"，
        还向 B 区广播一条并不存在的 upsert；要清零请用 `kind="delete"`（与 `_reindex_event` 的"正文不可拉→不臆造"同一口径）；
        ③**没有消费点的 kind 也入账**——`domain_move` 在执行档零效果，旧行为是从三个循环里掉出去＝账面看不见这一条；
        ④**`total` ＝ 批内真实条数**（空批＝0）——旧写法 `len(changes) or 1` 一个值两用（既当 B1 占比分母、又当回执条数），
        把"这次没有任何变更"报成"1 条变更、0 条删除"，目录批量面板照它念给用户；分母另算，回执只报真实数。
        ⑤**单条内部不原子＝已落地部分要点名**——一条 delete 是"摘两路→记墓碑→清清单"三步，中途抛错时前两步可能已生效
        （真实形态＝索引已摘而清单未清＝B5 背离债、且这条没有墓碑可恢复）；故失败行的原因里带"已摘几块／记了几条墓碑"
        与出路（`/api/ingest/reconcile` 看债、`/api/ingest/compensate` 按清单指针重放）。三步真做成原子＝需事务性双索引，
        属真实 PG＋Milvus 档（放行项②），本刀不假装给 dev 造一个。
        """
        if self._guard is not None:
            self._guard.ensure_write_allowed("ingest")
        total = len(changes)
        # 逐条账按**批内序号**（1 起）记，不按 kind 分组：运维拿到的"第几条"＝调用方提交的第几条，两套序号会让人对错账
        deletes = [(i, c) for i, c in enumerate(changes, start=1) if c.kind == "delete"]
        acl_changes = [(i, c) for i, c in enumerate(changes, start=1) if c.kind == "acl_change"]
        upserts = [(i, c) for i, c in enumerate(changes, start=1) if c.kind == "upsert"]
        unhandled = [(i, c) for i, c in enumerate(changes, start=1) if c.kind not in ("delete", "acl_change", "upsert")]
        if self._guard is not None:
            # 占比分母另算：空批的 `len(changes)` 是 0（除零），但**回执里的 total 必须是真实条数**——旧写法 `len() or 1`
            # 一处两用，把"没有变更"报成"1 条变更、0 条删除"，目录批量面板照它念给用户。
            self._guard.check("batch_delete_ratio", len(deletes) / (total or 1), "B1", partition="ingest")
        ts = now if now is not None else time.time()
        removed = 0
        evicted = 0
        reindexed = 0
        tombstoned = 0
        acl_blocks = 0
        acl_pending = 0
        acl_retired = 0
        items: list[dict[str, Any]] = []
        applied = blocked = failed = 0

        def _tally(c: ChangeEvent, seq: int, outcome: str, note: str) -> None:
            """一条变更＝一行账：批内序号＋结果（applied/blocked/failed）＋可指原因。批级计数只是这些行的求和。"""
            nonlocal applied, blocked, failed
            if outcome == "applied":
                applied += 1
            elif outcome == "blocked":
                blocked += 1
            else:
                failed += 1
            items.append(
                {"seq": seq, "doc_id": c.doc_id, "version": c.version, "kind": c.kind, "outcome": outcome, "detail": note}
            )

        for seq, c in deletes:
            outcome, note = "applied", "tombstone 已广播"
            start_removed = removed
            start_tomb = tombstoned
            try:
                ver = c.version or None  # 无 version=整 doc 删（全版本），有=指定版本（FR-ING-2 delete 精确化）
                affected = self._inventory.affected(c.doc_id, ver)
                if affected:
                    for ev, cids in affected:
                        r, e = self._purge(cids, list(ev.acl_tags))  # 两路索引+缓存同生同灭
                        removed += r
                        evicted += e
                        self._delete_ledger.record(ev, cids, deleted_at=ts)  # §10 软删除：记可恢复墓碑（正文留 objectstore）
                        tombstoned += 1
                        self._inventory.forget_version(c.doc_id, ev.version)  # 同步清账→reconcile 账实相符
                    note = f"两路同清 {removed - start_removed} 块、墓碑已记"
                else:
                    # 无登记信封（仅块 id 无事件，罕见）：仍精确删除，只是无指针=不进恢复账（不臆造可回滚假象）
                    chunk_ids = self._inventory.chunk_ids_for(c.doc_id, ver)
                    if chunk_ids:
                        r, e = self._purge(chunk_ids, sorted(set(c.acl_tags_before) | set(c.acl_tags_after)))
                        removed += r
                        evicted += e
                        note = f"无信封精确摘块 {len(chunk_ids)} 条（不进可恢复账）"
                        if ver is None:
                            self._inventory.forget_doc(c.doc_id)
                        else:
                            self._inventory.forget_version(c.doc_id, ver)
                    else:
                        outcome, note = "blocked", "零在册块（清单无登记）→ 只广播 tombstone，无可摘索引项"
                # FR-WIKI-3：删除广播 tombstone 给 B 区，把引用此源的发布页标"依据已失效"
                self._mq.publish(
                    FANOUT_TOPIC,
                    CanonicalEvent(
                        doc_id=c.doc_id,
                        version=c.version or 0,
                        domain=c.domain_before or "default",
                        op="delete",
                    ).model_dump(),
                )
            except Exception as exc:  # noqa: BLE001 - 逐条隔离：失败落账后继续跑其余条，末尾带账上抛
                # 单条 delete 内部是三步（摘两路→记墓碑→清清单），**不原子**：失败时必须写清"已落地哪几步"，
                # 否则 `failed` 一词会被读成"这条什么都没动"。真实现场＝索引已摘而清单未清 ⇒ B5 背离债、且无墓碑可恢复。
                landed = f"已摘两路索引 {removed - start_removed} 块、记墓碑 {tombstoned - start_tomb} 条"
                outcome, note = "failed", (
                    f"{type(exc).__name__}: {exc}｜本条不是零效果——{landed}；"
                    "单条内三步不原子：清单未清＝账实背离，先 GET /api/ingest/reconcile 看债、"
                    "再 POST /api/ingest/compensate 按清单指针重放补齐"
                )
            _tally(c, seq, outcome, note)

        # 重拉正文重放（F1：清单重拉与事件重放逐字节一致）——**正文取不到就不重放**，与 `_reindex_event` 同一口径
        for seq, c in upserts:
            key = f"{c.doc_id}/{c.version or 1}"
            outcome, note = "applied", f"正文重放入库（{key}）"
            try:
                if not self._objects.exists(key):
                    outcome, note = "blocked", f"正文不在册（objectstore 无 {key}）→ 不臆造空正文重放、不广播并不存在的更新"
                else:
                    raw = self._objects.get(key).decode("utf-8")
                    if not raw.strip():
                        outcome, note = "blocked", f"正文为空（{key}）→ 空正文重放＝把内容清零伪装成更新，清零请用 kind=delete"
                    else:
                        doc = Document(doc_id=c.doc_id, version=c.version or 1, raw_text=raw, acl_tags=c.acl_tags_after)
                        self.process_document(doc)
                        reindexed += 1
            except Exception as exc:  # noqa: BLE001 - 同上：逐条隔离
                outcome, note = "failed", f"{type(exc).__name__}: {exc}"
            _tally(c, seq, outcome, note)

        # FR-ING-5 acl_change 效应②（索引块 ACL 重写）：先重写块，再清缓存（③），顺序让"清"发生在"改"之后。
        # 效应①（PG 权限行）属放行项②（真实双存储），此处不伪造。
        # ④（wiki 页可见性重算＋回写块重合成）由 `_acl_rewrite` 的重放随 fan-out 兑现（见该方法），A 区不跨区改 B 区页。
        for seq, c in acl_changes:
            outcome, note = "applied", ""
            try:
                done, pend, dropped, retired = self._acl_rewrite(c, ts=ts)
                acl_blocks += done
                acl_pending += pend
                acl_retired += retired
                evicted += dropped  # 重写途中"先摘旧块"顺带清掉的缓存也入账，否则回执少报真实剔除数
                # FR-ING-5 acl_change 四效应之③（语义缓存按 ACL 域清）：前后域并集逐域剔除命中缓存。
                cleared = 0
                if self._cache is not None:
                    for tag in set(c.acl_tags_before) | set(c.acl_tags_after):
                        cleared += self._cache.invalidate_domain(tag)
                evicted += cleared
                note = f"重写 {done} 块／正文待补 {pend}／已下架 {retired}／顺带清缓存 {dropped + cleared} 条"
            except Exception as exc:  # noqa: BLE001 - 同上：逐条隔离
                outcome, note = "failed", f"{type(exc).__name__}: {exc}"
            _tally(c, seq, outcome, note)

        for seq, c in unhandled:
            _tally(c, seq, "blocked", f"执行档无 kind={c.kind} 的消费点（零效果；要落这一类动作须 ADR＝放行项㉑）")

        items.sort(key=lambda i: int(i["seq"]))  # 逐条账按提交顺序陈列（上面按 kind 分趟执行，执行序≠提交序）
        counters = {
            "total": total,
            "deleted": removed,
            "reindexed": reindexed,
            "acl_rewritten": acl_blocks,
            "acl_pending": acl_pending,
            "acl_retired": acl_retired,
            "cache_evicted": evicted,
            "applied": applied,
            "blocked": blocked,
            "failed": failed,
        }
        if failed:
            reasons = "; ".join(f"第{i['seq']}条 {i['doc_id']}/{i['version']}:{i['kind']}→{i['detail']}" for i in items if i["outcome"] == "failed")
            raise ChangeApplyError(
                f"{failed}/{len(changes)} 条变更失败：{reasons}（本批逐条账见 receipt['items']）",
                receipt={**counters, "items": items},
            )
        # 逐条明细**只随异常带出**：无失败＝回执保持 int-only（`pull` 的 `dict[str,int]` 与 `/api/ingest/dir` 回执形状不变）。
        # 因此"整批零失败但有 blocked"时只见计数不见原因——可达入口里 HTTP 只收 delete/acl_change，
        # 且两者各自的行（`deleted=0`＋`recoverable=0`＋`acl_pending`/`acl_retired`）都已把原因摆在计数上，无虚报；
        # 若以后放开 `upsert` 过门面，须同时把 items 透出＝在册残余（登记于 AGENTS.md §8 M4-cr）。
        return counters

    def _acl_rewrite(self, c: ChangeEvent, *, ts: float) -> tuple[int, int, int, int]:
        """FR-ING-5 效应②：把在册块的 ACL 按 `acl_tags_after` 重写进**两路索引与清单**；返回 (重写块数, 未能重写块数, 顺带清掉的缓存条数, 落在下架件上的收紧条数)。

        为什么走"重放"而不是"就地改标量"：dev 双索引按 chunk_id 覆写整条记录、块 ACL 就在记录里，重放即换域；
        企业档 Milvus 标量字段更新与 PG 权限行同源=放行项②，本刀不给 dev 造一个假接口。
        先 `_purge` 旧块再重放：块 id 由 doc_id/version/seq 确定性派生，正常应逐条覆写；但"先摘"让
        "新旧 id 万一不一致"不留残块——留着一条旧 ACL 的块＝收紧后仍可越权读到，属不可接受错值（双索引同生同灭）。
        `_purge` 按被摘块的旧域清缓存，与效应③ 同一动作：该计数一并回传，否则真实剔除数在回执里凭空少一笔
        （同域第二次清自然返回 0，不重复计）。
        正文拉不到（无登记信封/无指针）＝不臆造内容，计入 pending 并留在 B5 债里，绝不静默当已重写。
        重放经 `process_document`＝canonical fan-out 随行，效应④ 由 B 区在同一条流上兑现（见 `WikiLoop._compile`）。

        **逐版先入前值账再动手**（M4-cq）：账上的最新域是三路重放（恢复/补偿/重写）共同的受众口径，
        所以"改没改成索引"只影响 `applied_to` 与计数，不影响收紧本身的存续——下架件（清单无人、墓碑仍在）
        与正文缺失件都不再是静默 no-op。**前值一律取实值、不采信请求自述**（在册版取清单信封、下架版取账上最新域
        或墓碑记录的下架时受众）：`acl_tags_before` 在本方法零消费点——否则调用方报个假前值就能把账写歪。
        """
        ver = c.version or None
        affected = self._inventory.affected(c.doc_id, ver)
        rewritten = 0
        pending = 0
        dropped = 0
        covered: set[int] = set()
        for ev, cids in affected:
            covered.add(ev.version)
            before = list(ev.acl_tags)
            if not ev.store_key or not self._objects.exists(ev.store_key):
                self._audiences.record(c.doc_id, ev.version, before, list(c.acl_tags_after), ts=ts, applied_to="pending_body")
                pending += len(cids)
                continue
            self._audiences.record(c.doc_id, ev.version, before, list(c.acl_tags_after), ts=ts, applied_to="inventory")
            _r, e = self._purge(cids, sorted(set(ev.acl_tags) | set(c.acl_tags_after)))
            dropped += e
            if self._reindex_event(ev.model_copy(update={"acl_tags": list(c.acl_tags_after)})):
                rewritten += len(cids)
            else:  # 重放中途失败（正文读不出等）＝不假装换域成功，留在 pending 里交对账
                pending += len(cids)
        # 已下架的版本（清单已清账、墓碑仍在册）：收紧改的是受众账，恢复重放即按新域供答——不改写墓碑信封，
        # 墓碑要留"下架那一刻的事实"，两本账各记各的（改写＝账实分叉）。
        retired_keys = [k for k in self._delete_ledger.keys_for(c.doc_id, ver) if k[1] not in covered]
        for did, key_ver in retired_keys:
            # 前值优先级＝账上最新域 → 墓碑记录的下架时受众：连续两次收紧要接得上（第二次的前值是第一次的结果），
            # 而请求自述一律不采信（`acl_tags_before` 在本条零消费点）。
            latest = self._audiences.current(did, key_ver)
            retired_before = latest if latest is not None else (self._delete_ledger.audience_for(did, key_ver) or [])
            self._audiences.record(
                did, key_ver, retired_before, list(c.acl_tags_after), ts=ts, applied_to="retired"
            )
        return rewritten, pending, dropped, len(retired_keys)

    def admit(self, event: CanonicalEvent, chunks: list[Chunk]) -> int:
        """IngestionPort 实现：B 区回写块经此硬校验后入 A 区索引（跨区三禁之②）。

        F2 入口硬校验（纵深，封"跨源页面泄露"通道）：authority=="wiki" 的回写块 ACL 已由 B 区
        `compile._intersect_acl` 合成为全部源块交集（空则标 restricted），A 区入口**绝不按 event 回填或放大**，
        空 acl_tags 直接 fail-loud（禁索引成公开块）；source 块保持缺省按 event 回填的原行为。

        B0 写侧总闸同样覆盖此道：人审发布=写索引（§6"冻结全部写侧、保读侧"），拉闸期不得经回写绕过。
        权威块入索引后按其 ACL 域清一次语义缓存：发布承诺"该域引用即以 W 优先"，留着旧缓存就是等 TTL 才兑现。
        """
        if self._guard is not None:
            self._guard.ensure_write_allowed("wiki")
        for c in chunks:
            if c.authority == "wiki":
                if not c.acl_tags:
                    raise ProviderError(
                        f"回写块 {c.chunk_id} acl_tags 为空——F2 要求 wiki 侧交集合成（空应标 restricted），"
                        f"A 区入口禁按 event 回填放大（跨源页面泄露通道）"
                    )
                continue  # 回写块 ACL 视为已交集，不覆盖、不放大
            if not c.acl_tags and event.acl_tags:
                c.acl_tags = list(event.acl_tags)
        admitted = self._embed_and_index(chunks)
        # 回写块入账（M4-cd）：发布成功的权威块从此"应当已索引"，不入账则每一块都被 B5 报成孤儿（虚报淹没真背离）。
        # 只记成功入索引的（`admitted` 非 0），且按页归组：处置路径与源块不同——缺块靠 B 区重编译/重发布，不是重拉正文。
        if admitted:
            by_page: dict[str, list[str]] = {}
            for c in chunks:
                if c.authority == "wiki" and c.chunk_id:
                    by_page.setdefault(c.doc_id, []).append(c.chunk_id)
            for page_id, ids in by_page.items():
                self._inventory.record_writeback(page_id, ids)
        # 新权威块改变了该域的应答优先级：按块 ACL 域清语义缓存，否则"发布即以 W 优先"要等缓存过期才兑现（同 FR-ING-5③ 机制）。
        if admitted and self._cache is not None:
            for c in chunks:
                if c.authority == "wiki":
                    for tag in set(c.acl_tags):
                        self._cache.invalidate_domain(tag)
        return admitted

    def withdraw(self, chunk_ids: list[str], acl_tags: list[str]) -> int:
        """IngestionPort 撤回通道（与 admit 对称）：页不再是 published 时，把它的权威块从索引摘掉。

        不变式=「索引里的 wiki 权威块 ⇔ 该页 published」：源删除或源变更把页打成 retracted/stale 后，
        未复审的依据不得仍以 W 优先命中（FR-WIKI-3 的"依据已失效"必须真的停止供答案，不止于改状态标记）。
        仍是写侧动作，同过 B0 总闸（§6"冻结全部写侧"不因撤项而留旁路）；拉闸期撤不下＝复位后由重编译补。
        """
        if self._guard is not None:
            self._guard.ensure_write_allowed("wiki")
        removed, _evicted = self._purge(chunk_ids, acl_tags)
        # 撤账与撤索引必须同批（M4-cd）：只摘索引不销账，对账就把这张已停用页的块报成"应索引却缺"的幻影背离。
        self._inventory.forget_writeback(chunk_ids)
        return removed

    def _purge(self, chunk_ids: list[str], acl_tags: list[str]) -> tuple[int, int]:
        """把块从两路索引与语义缓存一并摘净：只清向量路会让 BM25 仍召回已删块（双索引同生同灭）。

        返回 (向量索引撤除数, 缓存清除数)；缓存按被撤块的 ACL 域清，与 FR-ING-5③ acl_change 同口径。
        """
        if not chunk_ids:
            return 0, 0
        removed = self._vectors.delete(chunk_ids)
        if self._lexical is not None:
            self._lexical.delete(chunk_ids)
        evicted = 0
        if self._cache is not None:
            for tag in set(acl_tags):
                evicted += self._cache.invalidate_domain(tag)
        return removed, evicted

    def _embed_and_index(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        model = self._embedding.model_id
        limit = self._embedding.input_token_limit
        # NFR-8：块 token 越 embedding 输入上界→显式失败（禁静默截断/降速），须回切块层缩块
        for c in chunks:
            if c.token_count > limit:
                raise ProviderError(
                    f"块 {c.chunk_id} token={c.token_count} 越 embedding 输入上界 {limit}（NFR-8 fail-loud，不得静默截断）"
                )
        for start in range(0, len(chunks), self._batch):
            batch = chunks[start : start + self._batch]
            vectors = self._embedding.embed([c.text for c in batch])
            for c, vec in zip(batch, vectors, strict=True):
                c.vector = vec
                c.embedding_model = model
                c.input_token_limit = limit
        if self._lexical is not None:
            self._lexical.index(chunks)
        return self._vectors.upsert(chunks)
