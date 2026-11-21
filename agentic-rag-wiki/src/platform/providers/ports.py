"""横切能力端口（Ports）。P3：分区只依赖本文件的协议，实现由组合根注入。

一切皆接插件：换 provider = 换实现/性能档（G5）。协议零 SDK import；
具体实现分文件放本包（`providers/<name>_<impl>.py`），也是 SDK 黑名单唯一豁免区。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from src.platform.types import Answer, AuditEvent, BreakerDecision, CanonicalEvent, Chunk, Document
from src.platform.types.document import PageState


class EmbeddingPort(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def model_id(self) -> str: ...

    @property
    def dim(self) -> int: ...

    @property
    def input_token_limit(self) -> int: ...


class RerankPort(Protocol):
    def rerank(self, query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]: ...


class LlmPort(Protocol):
    def complete(self, prompt: str, max_tokens: int = 512) -> str: ...


class CachePort(Protocol):
    """FR-CAP-1① 语义缓存存储档（键含 ACL 域指纹，键/准入逻辑在 service/ask/cache）。"""

    def get(self, key: str) -> Answer | None: ...

    def put(self, key: str, value: Answer) -> None: ...

    def invalidate_domain(self, domain: str) -> int:
        """FR-ING-5③ acl_change 效应：清除命中该 ACL 域的缓存条目，返回删除数（键首段=域指纹）。"""
        ...


class VectorStorePort(Protocol):
    def upsert(self, chunks: list[Chunk]) -> int: ...

    def delete(self, chunk_ids: list[str]) -> int: ...

    def search(self, vector: list[float], topk: int, acl_tags: list[str]) -> list[Chunk]: ...

    def alias_swap(self, old: str, new: str) -> None: ...

    def all_ids(self) -> list[str]: ...


class LexicalPort(Protocol):
    """BM25 词法召回端口（FR-ASK-5 漏斗第二路）：与向量路并列召回，后接 RRF 融合。

    dev 档纯 BM25 打分非质量基线；执行档换真实分词/jieba 只换实现（G5）。ACL 语义同向量路。
    """

    def index(self, chunks: list[Chunk]) -> int: ...

    def delete(self, chunk_ids: list[str]) -> int: ...

    def search(self, query: str, topk: int, acl_tags: list[str]) -> list[Chunk]: ...


class ObjectStorePort(Protocol):
    def put(self, key: str, data: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


class MqPort(Protocol):
    """事件队列端口（ADR-011＝租约消费）：`drain` 只**取得**、`ack` 才**确认**，未确认的一律可重投。

    为什么不是"drain 即消费"：单条事件失败（正文读不出、B1 判停、信封损坏）若发生在批量循环中间，
    drain 已把整批弹出，异常一抛剩余事件就凭空消失——账上既没落地也没积压，属静默错值里最危险的一种
    （比"少一块"更糟，因为连少都不报）。租约语义令**至少一次投递**成为端口的性质而非消费侧的自觉：
    消费侧按已完成前缀 `ack(topic, count)`，失败条与其后的全部退回队首（相对次序不变），
    积压数 `depth` 含未确认部分＝"卡住"这件事始终可见。
    换队列实现必须提供同语义（PG `SKIP LOCKED`＝行锁租约、Kafka＝不提交 offset 即重投）。
    """

    def publish(self, topic: str, payload: dict[str, object]) -> None: ...

    def drain(self, topic: str, max_items: int) -> list[dict[str, object]]:
        """取得（不确认）至多 `max_items` 条；返回的条目进入在租状态，未 ack 前仍计作积压。"""
        ...

    def ack(self, topic: str, count: int) -> int:
        """确认本轮在租条目的**前 `count` 条**已消费，其余退回队首；返回退回条数。

        `count` 大于在租条数＝消费侧记账错乱，必须 fail-loud（不得静默夹紧把"少确认"读成"都消费完了"）。
        """
        ...

    def depth(self, topic: str) -> int:
        """未消费积压（E4 "摄取积压" RED 指标的传感原语）：**含在租未确认部分**，入端口契约，换队列实现必须提供同语义 backlog。
        注：E4 仪表盘/CI 上线核验属运行时候选档（非离线可决），此处仅锁定指标原语的端口契约与可换性。"""
        ...


class OcrPort(Protocol):
    """页态旁路供给（FR-ING-8 第⓪刀的执行位，`IngestPipeline._resolve_scan` 消费）。

    与 `ScanOcrPort`（providers 本地协议）的分工＝**门禁 vs 供给**：像素级的真认字发生在解析边界
    （`FileParserPort.parse` 那儿才有字节），管线这一层拿到的是已带正文与 `page_state` 的 `Document`，
    职责是"scanned/mixed 必须有正文来源、过质量门、失败页不进索引"，不是再认一遍（重复 OCR＝同一页两种字）。
    dev 档 `PassthroughOcr` 原样透传 `raw_text`＝演练同一条门禁路径，**不冒充 OCR 结论**。
    """

    def recognize(self, document: Document) -> str: ...


class ParsedBody(Protocol):
    """`FileParserPort.parse` 的产物形状（ADR-009）：正文＋三态页态＋两条页码账。

    `ocr_pages`＝本次由 OCR 供给的页、`failed_pages`＝OCR 后仍无字而被排除的页（FR-ING-8"失败页不进索引"）。
    两个账目必须随正文一起交出去：只报"抽出了 N 字符"而藏起失败页＝把残缺文档冒充完整文档入库。
    """

    @property
    def text(self) -> str: ...

    @property
    def page_state(self) -> PageState: ...

    @property
    def ocr_pages(self) -> Sequence[int]: ...

    @property
    def failed_pages(self) -> Sequence[int]: ...


class FileParserPort(Protocol):
    """文件解析端口（摄取链上游）：把上传文件字节抽成**正文＋页态**，正文进 `Document.raw_text` 走管线。

    一切皆插件（G5）：dev 档=stdlib OOXML + pypdf 文本层 + legacy OLE 经本机 Office 转换（M4-ce 1c）
    + 扫描版 PDF 经本机 OCR（M4-cf，放行项⑦）；企业档换真实版面解析/云 OCR 只换实现，
    `IngestPipeline` 与门面不感知。
    `supported` 供门面预筛，`parse` 失败/不支持一律 fail-loud（抛错转 4xx），绝不静默给空文本。

    返回值取 `ParsedBody` 形状而非裸 str（ADR-009）：**页态必须由解析处给出**——三态探测（FR-ING-8）判据
    是"每页有无文本层"，只有摸过容器的人才知道；让门面事后猜（或一律按 digital 入库）＝同一份文档两种答案，
    属本项目反复拦的口径分叉族（M4-aj..au/M4-ak）。非 PDF 格式按 `digital` 报，语义不变。
    """

    def supported(self, filename: str) -> bool: ...

    def parse(self, filename: str, data: bytes) -> ParsedBody: ...


class GraphPort(Protocol):
    """编排端口：声明式跑摄取 DAG / agent 问答状态图；换 langgraph 只换实现（ADR-001）。"""

    def run(self, steps: Sequence[object], state: object) -> object: ...

    def run_while(
        self,
        steps: list[Callable[[object], object]],
        state: object,
        stop: Callable[[object], bool],
        max_steps: int,
    ) -> object:
        """条件边回路：反复跑 steps，遇 stop(state) 或耗尽 max_steps 收敛（FR-ASK-1 低置信→再检索）。"""
        ...


class IngestionPort(Protocol):
    """B→A 唯一通道（跨区三禁之②）：wiki 回写/撤回/依据核都走此端口，不直写 A 区索引、不 import A 区。"""

    def admit(self, event: CanonicalEvent, chunks: list[Chunk]) -> int: ...

    def withdraw(self, chunk_ids: list[str], acl_tags: list[str]) -> int:
        """与 admit 对称的撤回通道：页离开 published 时，把它的权威块从索引摘掉。

        `acl_tags`=被撤块原 ACL 域，供语义缓存按域清（FR-ING-5③ 同一口径）；返回撤出的向量索引块数。
        """
        ...

    def is_indexed(self, doc_id: str) -> bool:
        """该源 doc 当前是否仍有登记在册的块（依据存活判据）。

        存在的理由＝**A 区清单只有一本**：B 区若另记一份"哪些源已删"，两套账迟早漂移（重启即失忆、恢复后仍判已死）。
        发布门用它问"这张页的依据还在不在"（FR-WIKI-3），而不是靠 B 区进程内的墓碑集合。
        """
        ...


class GuardPort(Protocol):
    """熔断闸端口：分区把可疑批量操作/供给指标交它判定，实现（组合根）负责 shadow/硬断 + 审计。

    分区只依赖本协议、不自判阈值（阈值是 platform/breaker 的禁改区，机检见 harness）。
    """

    def check(
        self, metric: str, value: float, breaker: str, partition: str = "platform"
    ) -> BreakerDecision: ...

    def ensure_write_allowed(self, partition: str = "platform") -> BreakerDecision:
        """写侧入口预检（B0 总闸）：连续未复位硬断达阈→拉闸冻结写侧、抛 BreakerTripped；未达阈放行。"""
        ...


class AuditPort(Protocol):
    """C 区审计端口（FR-MEM-1，append-only）：各分区经此落事件，绝不 import memory 分区实现。

    跨区三禁之①：B/A 区不得直接 import C 区，写审计一律走本端口，由组合根注入实现。
    """

    def emit(self, event: AuditEvent) -> None: ...


class InflightPort(Protocol):
    """在飞 LLM 并发预算端口（FR-CAP-3 / B2）：问答出口用 `slot()` 包住 LLM 调用即进出记账。

    A 区 runtime 只依赖本协议（不 import gateway 的具体 tracker，守 P3 反向依赖）。
    """

    def slot(self) -> AbstractContextManager[object]: ...
