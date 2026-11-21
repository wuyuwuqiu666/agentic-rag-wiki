"""组合根（唯一注入点，P3/§6.1）：按装配表把端口接成实现，产出 App + 摄取/学习回路。

全仓唯一同时可见 provider 具体实现与三分区的模块；其余跨区触达一律经端口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.partitions.ingest.repo.canonical_inventory import CanonicalInventory
from src.partitions.ingest.runtime.pipeline import IngestPipeline
from src.partitions.ingest.service.ask.funnel import FunnelLedger
from src.partitions.ingest.service.ask.ladder import ladder_level
from src.partitions.memory.repo.portrait_store import DevPortraitStore
from src.partitions.memory.service.audit_sink import AuditSink
from src.partitions.wiki.runtime.loop import WikiLoop
from src.platform.breaker.pool import PoolBudget, PoolLedger
from src.platform.config import Settings, get_settings
from src.platform.config.settings import configure_logging
from src.platform.gateway.admin import Admin
from src.platform.gateway.app import App
from src.platform.gateway.audit_adapter import AuditAdapter
from src.platform.gateway.inflight import InflightTracker
from src.platform.gateway.policy_gate import PolicyGate
from src.platform.harness.breaker_rules.loader import load_rules
from src.platform.providers import (
    CachePort,
    EmbeddingPort,
    FileParserPort,
    GraphPort,
    GuardPort,
    LexicalPort,
    LlmPort,
    MqPort,
    ObjectStorePort,
    OcrPort,
    RerankPort,
    VectorStorePort,
)
from src.platform.providers.bm25_dev import Bm25Index
from src.platform.providers.cache_memory import MemoryCache
from src.platform.providers.embedding_cloud import CloudEmbedding
from src.platform.providers.embedding_dev import DevHashEmbedding
from src.platform.providers.embedding_local import LocalEmbedding, safe_batch_for_vram
from src.platform.providers.fileparser_dev import DevFileParser
from src.platform.providers.llm_cloud import CloudLlm
from src.platform.providers.llm_dev import DevEchoLlm
from src.platform.providers.llm_local import LocalLlm
from src.platform.providers.llm_router import LlmRouter
from src.platform.providers.mq_kafka import KafkaMq
from src.platform.providers.mq_pgqueue import PgQueueMq
from src.platform.providers.objectstore_fs import FileSystemObjectStore
from src.platform.providers.objectstore_s3 import S3ObjectStore
from src.platform.providers.ocr_dev import PassthroughOcr
from src.platform.providers.orchestration_langgraph import LangGraphOrchestration
from src.platform.providers.orchestration_plain import PlainOrchestration
from src.platform.providers.rerank_cloud import CloudRerank
from src.platform.providers.rerank_dev import DevLexicalRerank
from src.platform.providers.rerank_local import LocalRerank
from src.platform.providers.vectorstore_memory import MemoryVectorStore
from src.platform.providers.vectorstore_milvus import MilvusVectorStore
from src.platform.providers.vectorstore_pgvector import PgVectorStore
from src.platform.types import AuditEvent


@dataclass
class Container:
    settings: Settings
    embedding: EmbeddingPort
    vectorstore: VectorStorePort
    lexical: LexicalPort
    objectstore: ObjectStorePort
    mq: MqPort
    rerank: RerankPort
    llm: LlmPort
    guard: GuardPort
    inflight: InflightTracker
    pools: PoolLedger  # §8.5 B2 分池预算三本独立账（问答/wiki 编译/进化作业）
    graph: GraphPort
    cache: CachePort
    funnel: FunnelLedger  # §8.5 FR-CAP-1 四层命中账本（问答链记、Admin 读同一册）
    fileparser: FileParserPort  # 文件摄取上游（docx/pptx/pdf/txt/md + doc/ppt 经 Office 转换→纯文本），供 /api/ingest/file 门面委派
    ingest: IngestPipeline
    wiki: WikiLoop
    audit: AuditSink
    portraits: DevPortraitStore  # C 区长期聚合画像（角色→可见知识域）；HTTP 门面的后端划分读这册
    app: App
    admin: Admin
    # M4-cd：canonical 清单账（源块册 + 回写块册）的把手——B5 对账读的就是这一册，运维读回不得另建第二份账。
    inventory: CanonicalInventory
    # M4-ce 1b 目录批量入库位点（doc_id→正文指纹）：进程内账，跨进程/重启持久＝放行项②（诚实账，勿当已持久化）。
    dir_ingest_state: dict[str, str] = field(default_factory=dict)


def _local_batch_safe(s: Settings) -> int:
    """NFR-8 反推的装配口径：显存预算 + 每批占用齐备→按 `vram_safety_factor` 反推 batch 上界；
    任一未给（真机显存实测=放行档）→直接用配置化 `local_batch_safe`（默认路径行为不变）。"""
    if s.local_vram_mb and s.local_per_batch_mb:
        return safe_batch_for_vram(s.local_vram_mb, s.local_per_batch_mb, safety_factor=s.vram_safety_factor)
    return s.local_batch_safe


def build_container(settings: Settings | None = None) -> Container:
    s = settings or get_settings()
    # 装配期一次性引导：应用 `Settings.log_level`（组合根=唯一启动入口，非法级别 fail-loud 拒装配）。
    configure_logging(s.log_level)
    c = s.providers
    batch_safe = _local_batch_safe(s)
    embedding: EmbeddingPort
    if c.embedding == "cloud":
        embedding = CloudEmbedding(s.cloud_embedding_base_url, s.cloud_embedding_api_key)
    elif c.embedding == "local":
        embedding = LocalEmbedding(
            s.local_embedding_model,
            batch_safe=batch_safe,
            model_dir=s.local_embedding_model_dir,
            input_token_limit=s.local_input_token_limit,
        )
    else:
        embedding = DevHashEmbedding(dim=s.local_embedding_dim)

    vectorstore: VectorStorePort
    if c.vectorstore == "milvus":
        vectorstore = MilvusVectorStore(s.milvus_uri)
    elif c.vectorstore == "pgvector":
        vectorstore = PgVectorStore(s.pg_dsn)
    else:
        vectorstore = MemoryVectorStore()
    lexical: LexicalPort = Bm25Index()
    # G5/ADR-005：消费 c.objectstore / c.mq（此前二者零读、硬写 fs/pg_queue=选 s3/kafka 静默错配，同 M4-an/ap）。
    # M4-bu 真实现（放行项⑯）：s3→boto3 适配、kafka→confluent-kafka 适配；桶名/broker 取自 Settings，
    # 缺桶/缺 broker/缺驱动均 fail-loud，绝不静默回落默认档（真·live bucket/broker 往返属企业放行档、不伪造）。
    objectstore: ObjectStorePort = (
        S3ObjectStore(s.s3_bucket, endpoint_url=s.s3_endpoint_url)
        if c.objectstore == "s3"
        else FileSystemObjectStore(f"{s.artifact_dir}/canonical-draft")
    )
    mq: MqPort = KafkaMq(s.kafka_broker) if c.mq == "kafka" else PgQueueMq()
    rerank: RerankPort
    if c.rerank == "cloud":
        rerank = CloudRerank(s.cloud_rerank_base_url, s.cloud_rerank_api_key)
    elif c.rerank == "local":
        rerank = LocalRerank(s.local_rerank_model)
    else:
        rerank = DevLexicalRerank()

    audit = AuditSink(s.audit_dir)
    audit_port = AuditAdapter(audit)
    guard = PolicyGate(load_rules(), audit, shadow_only=s.shadow_breaker_set)
    # 审计 #2／ADR-012（M4-cs）：重启**不是**双人复位的旁路——建容器时从 append-only 审计账本回放未复位硬断，
    # 达阈即继续冻结写侧。只读不写审计；账本读不懂则拒起（fail-loud，绝不静默解冻）。
    guard.restore_from_audit()
    # §8.5 B2 分池预算：三本独立账（问答/wiki 编译/进化作业）共享一册账本，额度按全局硬上限等比定档；
    # 问答出口=在线池视图（strict=False，B2 软告警不阻断在线），离线作业上线即自动受同一账本执法。
    pool_budget = PoolBudget.for_hard(s.inflight_llm_limit)
    pools = PoolLedger(pool_budget)
    inflight = InflightTracker(guard, ledger=pools)

    # FR-CAP-2 读侧降档信号源：在飞总数对 B2 soft(峰水位)/hard 判档（与 b2.yaml 同一口径、不另立阈值表）。
    def _inflight_level() -> int:
        return ladder_level(pools.total(), soft=pool_budget.peak, hard=pool_budget.hard)

    # FR-ASK-4 双端点路由：cloud 主档→本地兜底（R11 断云自动切本地，不出网）；主档失败经审计上报但不判 B3。
    def _on_fail(endpoint: str, exc: Exception) -> None:
        audit_port.emit(
            AuditEvent(kind="provider_fail_loud", partition="gateway", detail=f"llm {endpoint} 失败已回退：{exc}")
        )

    llm: LlmPort
    if c.llm == "cloud":
        llm = LlmRouter(
            CloudLlm(s.cloud_llm_base_url, s.cloud_llm_api_key),
            fallback=LocalLlm(),
            canary=None,
            canary_ratio=s.llm_canary_ratio,
            on_fail=_on_fail,
        )
    elif c.llm == "local":
        llm = LocalLlm()
    else:
        llm = DevEchoLlm()

    # FR-ASK-1/ADR-001 编排档：消费 c.orchestration（此前零读、硬写 plain=静默错配，M4-ap 收口）。默认 plain；
    # 选 langgraph→LangGraphOrchestration 真 StateGraph 实现（放行项⑮ 已装 langgraph 1.2.11/MIT，M4-bt 落地，
    # plain↔langgraph 同 GraphPort 双契约套件见 tests/contract/test_graph_dual_backend.py）；缺驱动即构造 fail-loud、
    # 绝不静默回落 plain（同 M4-an pgvector）。真·跨进程/PG checkpoint + interrupt 人审真接线仍企业档（见放行项⑮ 残余）。
    graph: GraphPort = LangGraphOrchestration() if c.orchestration == "langgraph" else PlainOrchestration()

    # FR-CAP-1① 语义缓存（dev 内存精确键档；企业档=向量相似召回，键/准入不变，G5 可换）。
    cache: CachePort = MemoryCache()
    # FR-CAP-1 命中率稳态口径账本：问答链按层归属记入，运维面经 `Admin.funnel_stats` 读回（同一册对象）。
    funnel_ledger = FunnelLedger()

    # FR-ING-8 三态探测的**准入侧**：dev=PassthroughOcr（把解析边界已识别的正文交质量门，不认第二遍）。
    # 真识别在供给侧 `ScanOcrPort`（M4-cf 放行项⑦ 闭合＝本机 RapidOCR，见下方 fileparser 装配）；OcrPort 接口不变＝G5 可换云 OCR/PaddleOCR。
    ocr: OcrPort = PassthroughOcr()

    inventory = CanonicalInventory()
    ingest = IngestPipeline(
        embedding,
        vectorstore,
        objectstore,
        mq,
        batch_safe=batch_safe,
        guard=guard,
        audit=audit_port,
        inventory=inventory,
        lexical=lexical,
        cache=cache,
        ocr=ocr,
        reconcile_dir=Path(f"{s.artifact_dir}/reconcile"),
    )
    # B0 拉闸期＝全部写侧冻结：wiki 编译（建页/标脏/撤块/回写）属写侧，故同一册 guard 注入 B 区回路，
    # 且判门在 `drain` 之前（ADR-011）＝冻结期零消费，fan-out 积压留在队列里可见、不蒸发。
    wiki = WikiLoop(mq, ingest, objectstore, audit=audit_port, guard=guard)
    app = App(
        s, embedding, vectorstore, rerank, llm,
        guard=guard, audit=audit_port, lexical=lexical, inflight=inflight, graph=graph, cache_store=cache,
        funnel_ledger=funnel_ledger, degrade_level=_inflight_level,
    )
    # §5 gateway "admin API/CLI"：运维侧审计回放（U4）与双人复位（§10）经组合根接成可达入口。
    admin = Admin(audit, guard, pools, funnel_ledger)
    # 文件解析档（G5 可换）：dev=stdlib OOXML + 旧版 OLE 经本机 Office 转换 + pypdf 文本层 + **扫描页经本机 RapidOCR 识别**（缺装即失败页如实记账）。
    fileparser: FileParserPort = DevFileParser()
    # FR-MEM-2 读侧：角色聚合画像册（dev 单机 JSON 快照，与 append-only 审计分文件；PG 物理分表=放行项②）。
    portraits = DevPortraitStore(f"{s.artifact_dir}/portraits.json")
    return Container(
        s, embedding, vectorstore, lexical, objectstore, mq, rerank, llm,
        guard, inflight, pools, graph, cache, funnel_ledger, fileparser, ingest, wiki, audit, portraits, app, admin,
        inventory,
    )
