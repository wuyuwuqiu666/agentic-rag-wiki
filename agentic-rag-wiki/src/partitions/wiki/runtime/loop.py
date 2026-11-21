"""B 区学习回路：消费 canonical fan-out → triage → 草稿；发布时经 IngestionPort 回写 A 区。

跨区三禁之②：本模块只持注入的 `IngestionPort` 抽象，绝不 import A 区（ingest）。
"""

from __future__ import annotations

from collections.abc import Callable

from src.partitions.wiki._types import TriageSignals
from src.partitions.wiki.repo.pages import PageRegistry
from src.partitions.wiki.service import compile, review, triage
from src.platform.providers import AuditPort, GuardPort, IngestionPort, MqPort, ObjectStorePort
from src.platform.types import AuditEvent, CanonicalEvent, Chunk, WikiPage

FANOUT_TOPIC = "canonical.fanout"


class WikiLoop:
    def __init__(
        self,
        mq: MqPort,
        ingestion: IngestionPort,
        objectstore: ObjectStorePort,
        audit: AuditPort | None = None,
        *,
        guard: GuardPort | None = None,
        pins: set[str] | None = None,
        signal_provider: Callable[[CanonicalEvent], TriageSignals] | None = None,
        registry: PageRegistry | None = None,
    ) -> None:
        self._mq = mq
        self._ingestion = ingestion
        self._objects = objectstore
        self._audit = audit
        # B0 总闸的读侧预检（§6"冻结全部写侧、保读侧"）：编译=写页+写回，属写侧动作，闸门必须在取事件之前。
        self._guard = guard
        self._pins = pins or set()  # 管理员圈定的 doc_id（FR-WIKI-4 圈定信号）
        self._signal_provider = signal_provider  # 缺省=仅静态属性；演练时注入合成行为信号
        self._registry = registry or PageRegistry()
        # 编译时的源事件信封（page_id→CanonicalEvent）：发布回写 admit 所需，随页留存。
        # 诚实边界=进程内内存态（dev 档），进程重启即缺信封→入口 fail-loud 要求重编译，绝不凭页内容伪造信封（F1 语义）。
        self._source_events: dict[str, CanonicalEvent] = {}
        self.drafts: list[WikiPage] = []

    @property
    def pages(self) -> PageRegistry:
        return self._registry

    def page(self, page_id: str) -> WikiPage | None:
        """按 page_id 取单页（人审台读回入口，供门面 1:1 委派）。"""
        return self._registry.get(page_id)

    def all_pages(self) -> list[WikiPage]:
        return self._registry.all()

    def source_event(self, page_id: str) -> CanonicalEvent | None:
        """本页编译时携带的 canonical 信封；缺即 None（调用方须 fail-loud，不得伪造 domain/acl/store_key）。"""
        return self._source_events.get(page_id)

    def _signals(self, event: CanonicalEvent) -> TriageSignals:
        if self._signal_provider is not None:
            return self._signal_provider(event)
        return TriageSignals(doc_type=event.doc_type, token_count=event.token_count, admin_pinned=event.doc_id in self._pins)

    def run_once(self) -> int:
        """消费 canonical fan-out：`op=upsert`→triage 入选建草稿（源变只标脏本域页）；`op=delete`→发布页标失效。

        返回本轮新草稿数（FR-WIKI-2 增量：只重算受影响 doc 的页，不整库重跑）。

        **判门前置＋租约消费（ADR-011，审计 #6）两条都是不可省的次序**：
        ① 闸在 `drain` 之前——B0 拉闸期若先取后判，取走的整批事件既没编译也没人还，等于"冻结写侧"用一次
        消费把 fan-out 直接吃掉；先判门则拉闸期**零 drain、零消费**，积压原封不动留在队列里、`depth` 看得见。
        ② `ack` 只确认**已完成前缀**——中途抛错（信封破坏、正文读不出、`_compile` 里任何 fail-loud）时，
        出错那条连同其后全部退回队首；原实现 drain 即消费，一次中途失败静默销毁最多 1000 条事件，
        既不在页上也不在积压里＝无从发现。异常照旧上抛（不吞、不假装跑完），重投由租约语义兑现＝至少一次。
        """
        if self._guard is not None:
            self._guard.ensure_write_allowed("wiki")  # 效应＝读侧照常、写侧（含本页回路）全停
        events = self._mq.drain(FANOUT_TOPIC, max_items=1000)
        made = 0
        done = 0
        try:
            for raw in events:
                event = CanonicalEvent.model_validate(raw)
                if event.op == "delete":
                    self._invalidate(event)
                else:
                    made += self._compile(event)
                done += 1
        finally:
            self._mq.ack(FANOUT_TOPIC, done)  # 未 ack 的退回队首重投：已完成前缀不因后面的失败而重做
        return made

    def _compile(self, event: CanonicalEvent) -> int:
        page_id = f"wiki/{event.domain}/{event.doc_id}"
        prior = self._registry.get(page_id)
        if prior is not None and prior.status == "published":
            # FR-WIKI-2：源变更→已发布页标脏，重编译时顶替（不动其它页）；脏页的依据未复审，其权威块同步撤下
            review.mark_stale(prior)
            self._withdraw(prior, f"源 {event.doc_id} 变更→页标脏")
        selected, reason = triage.should_compile(self._signals(event))
        if not selected:
            return 0
        body = self._objects.get(event.store_key).decode("utf-8") if event.store_key else ""
        pseudo = Chunk(
            chunk_id=f"{event.doc_id}/src",
            doc_id=event.doc_id,
            doc_version=event.version,
            seq=0,
            text=body,
            acl_tags=list(event.acl_tags),
        )
        draft = compile.compile_draft(event, [pseudo], title=event.doc_id)
        draft.triage_reason = reason
        self._registry.put(draft)
        self._source_events[draft.page_id] = event  # 溯源：发布回写用的信封与页同批留存
        self.drafts.append(draft)
        return 1

    def _invalidate(self, event: CanonicalEvent) -> None:
        """FR-WIKI-3：源删除→引用它的已发布页标"依据已失效"（published→retracted），撤其权威块并留痕审计。"""
        for page in self._registry.pages_for_doc(event.doc_id):
            if page.status == "published":
                review.retract(page)
                self._withdraw(page, f"源 {event.doc_id} 删除→依据已失效")

    def _withdraw(self, page: WikiPage, reason: str) -> None:
        """页离开 published＝它的块不再够格当权威答案：经 IngestionPort 撤回（§6.1 三禁②，不直写 A 区索引）。

        块 id 由 `compile.build_writeback` 同一纯函数推出，绝不另立第二套命名口径（口径分叉＝撤不到）。
        """
        dropped = self._ingestion.withdraw([c.chunk_id for c in compile.build_writeback(page)], list(page.acl_tags))
        self._emit(
            AuditEvent(
                kind="wiki_invalidate",
                partition="wiki",
                doc_id=page.page_id,
                detail=f"{reason}，撤下权威块 {dropped} 个（未复审的依据不再以 W 优先供答案）",
            )
        )

    def submit_for_review(self, page: WikiPage) -> None:
        """提交人审：draft → in_review（发布的前置门）。"""
        review.submit_for_review(page)

    def reject(self, page: WikiPage) -> None:
        """打回：in_review → draft。"""
        review.reject(page)

    def publish(self, page: WikiPage, event: CanonicalEvent, *, reviewer: str) -> int:
        """人审通过后发布→生成回写块→交 IngestionPort 入 A 区索引（authority=wiki，检索优先）。

        **人审门（FR-WIKI-1）**：页须处于 `in_review` 且 `reviewer` 署名非空，否则 `ReviewError`——
        草稿不得直接发布，把"人审后才生效"钉成代码不变式。
        **依据存活门（FR-WIKI-3）**：页的每条 `source_doc_ids` 须仍在 A 区清单在册，否则同样 `ReviewError`——
        源已删除/下架时，未发布的草稿（`_invalidate` 只撤 published 页，够不着它们）否则可被署名"复活"成权威块，
        让依据已失效的内容以 W 优先供答案。判据取自 `IngestionPort.is_indexed`（A 区清单＝唯一真相），
        B 区不自立第二本"哪些源已删"的账：两本账迟早漂移（重启失忆＝误拒、恢复未同步＝误放）。
        顺序=先判门（零副作用）→再写 A 区→最后改本页状态：写侧被拒（B0 拉闸、F2 空 ACL、NFR-8 越界）时
        页仍留在 `in_review`，不出现"页已 published 而索引里没有"的账实背离。
        """
        self._require_live_sources(page)
        review.check_approve(page, reviewer)
        writeback = compile.build_writeback(page)
        admitted = self._ingestion.admit(event, writeback)
        review.approve(page, reviewer)
        self._registry.put(page)  # 确保页按 source doc 建倒排（供后续变更/失效定位）
        self._emit(
            AuditEvent(
                kind="wiki_publish",
                partition="wiki",
                doc_id=page.page_id,
                detail=f"发布回写 {admitted} 块，ACL 交集={page.acl_tags}，审阅人={reviewer}",
            )
        )
        return admitted

    def _require_live_sources(self, page: WikiPage) -> None:
        """依据存活门（零副作用）：页列出的每条依据源必须仍在 A 区在册，否则不得发布。"""
        dead = [doc_id for doc_id in page.source_doc_ids if not self._ingestion.is_indexed(doc_id)]
        if dead:
            raise review.ReviewError(
                f"依据已失效：源 {dead} 已删除/下架（不在 A 区清单在册），草稿不得升为权威。"
                f"处置＝先按 §10 恢复该源或等新版本入库并重编译，再送审发布"
            )

    def _emit(self, event: AuditEvent) -> None:
        if self._audit is not None:
            self._audit.emit(event)
