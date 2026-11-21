from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EmbeddingBackend = Literal["local", "cloud", "dev_hash"]
RerankBackend = Literal["local", "cloud", "dev_lexical"]
# dev_echo=仅 M0 闭环占位（模板回显，不代表模型质量）；执行档目标档=cloud（M2 接真实客户端后切）
LlmBackend = Literal["cloud", "local", "dev_echo"]
VectorBackend = Literal["pgvector", "milvus", "memory"]
StoreBackend = Literal["filesystem", "s3"]
MqBackend = Literal["pg_queue", "kafka"]
GraphBackend = Literal["plain", "langgraph"]


def hosts_of(allowlist_csv: str) -> set[str]:
    """出网白名单 CSV → 规范化主机名集合（小写、去空白、丢空项）。"""
    return {h.strip().lower() for h in allowlist_csv.split(",") if h.strip()}


def host_of(base_url: str) -> str:
    """从 base_url 抽主机名：剥 scheme、剥路径、剥端口、小写。"""
    host = base_url.split("://", 1)[-1]  # 去 scheme（http/https/其它）
    host = host.split("/", 1)[0]  # 去路径
    host = host.split(":", 1)[0]  # 去端口
    return host.strip().lower()


def egress_host_allowed(base_url: str, allowlist_csv: str) -> bool:
    """base_url 主机是否落在出网白名单内。空白名单=不启用预检（dev 档），一律放行。"""
    allow = hosts_of(allowlist_csv)
    if not allow:
        return True
    return host_of(base_url) in allow


def resolve_log_level(raw: str) -> int:
    """`Settings.log_level`（字符串）→ 数值日志级别（stdlib logging）。

    外部输入先校验（T2）：非法级别名 fail-loud 抛 `ValueError`，不静默回退 INFO——
    否则 `log_level=DEBUGG`（拼写错）会悄悄按 INFO 跑、与运维预期背离（NFR-8 同口径）。
    """
    level = logging.getLevelName(raw.strip().upper())
    if not isinstance(level, int):
        raise ValueError(f"未知日志级别：{raw!r}（须为 DEBUG/INFO/WARNING/ERROR/CRITICAL 之一）")
    return level


def configure_logging(raw_level: str) -> int:
    """装配期日志引导：解析 `log_level` 并应用到根 logger，返回生效级别（供审计/自检）。

    stdlib `logging.basicConfig` 在根 logger 已有 handler 时为空操作（幂等），故组合根重复调用/测试环境安全。
    """
    level = resolve_log_level(raw_level)
    logging.basicConfig(level=level)
    return level


class ProviderChoices(BaseModel):
    """装配表：逐端口定档（ADR-005/v0.7.1，禁止全局一刀切）。

    下表默认值=**M0 可跑通档**（零外部依赖）；执行档目标档=embedding:local / rerank:local / llm:cloud，
    接真实实现后由 .env 覆盖（G5：换实现只改本表，业务代码零改动）。
    """

    embedding: EmbeddingBackend = "dev_hash"
    rerank: RerankBackend = "dev_lexical"
    llm: LlmBackend = "dev_echo"
    vectorstore: VectorBackend = "memory"
    objectstore: StoreBackend = "filesystem"
    mq: MqBackend = "pg_queue"
    orchestration: GraphBackend = "plain"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"  # 装配期经 configure_logging/resolve_log_level 应用到根 logger（非法级别 fail-loud）
    artifact_dir: str = "workspace"
    audit_dir: str = "workspace/audit"

    # 里程碑启用 shadow 的闸（逗号分隔）：M1 期 B1/B5 只记不拦（观察再硬断）；M4 清空即全硬断。
    shadow_breakers: str = "B1,B5"

    providers: ProviderChoices = Field(default_factory=ProviderChoices)

    # 本地模型锚（ADR-004）
    local_embedding_model: str = "qwen3-embedding-0.6b"
    local_embedding_model_dir: str | None = None  # ADR-004：本地权重/缓存目录（ModelScope 落盘路径）；给定则 LocalEmbedding 从此加载，缺省回退框架默认缓存
    local_embedding_dim: int = 256  # M0 哈希实现档维度；生产=1024（MRL）
    local_input_token_limit: int = 32768  # ADR-004：本地 embedding 输入上界（NFR-8 门限唯一口径源，注入 LocalEmbedding→pipeline 拦截块超限）
    local_rerank_model: str = "bge-reranker-v2-m3"
    local_batch_safe: int = 32  # NFR-8：显存×0.9 反推的安全上界（配置化）
    vram_safety_factor: float = 0.9  # NFR-8：反推系数；齐备 local_vram_mb+local_per_batch_mb 时经 safe_batch_for_vram 消费（真机显存实测=放行档）
    # NFR-8 反推输入：显存预算与每批占用（实测口径）皆给定→按 ×0.9 反推 batch 上界；任一缺省→直接用 local_batch_safe（默认路径不变）
    local_vram_mb: int | None = None
    local_per_batch_mb: int | None = None

    # 执行档容量参数（§8.8 左列）
    inflight_llm_limit: int = 8
    agent_max_hops: int = 8
    retrieval_vector_topk: int = 100
    retrieval_bm25_topk: int = 100
    rrf_k: int = 60
    rerank_candidates: int = 50
    answer_topn: int = 8
    answer_topn_min: int = 5
    # FR-ING-7 邻居回取：overlap 默认 0，检索期按 prev/next 从候选池补邻块重建上下文（C5 预算内的补入 token 上限）。
    neighbor_refetch: bool = True
    neighbor_refetch_tokens: int = 2000
    reject_threshold: float = 0.35  # 安全参数（FR-MEM-4：只提案，不自动改）
    agent_confidence_target: float = 0.7  # 多跳回路收敛目标：低于此值则扩写查询再检索（≤agent_max_hops）
    llm_canary_ratio: int = 0  # FR-ASK-4 灰度百分比（0..100）：确定性计数分流到 canary 端点
    # §15 E4 LLM 逐次成本核算单价（元/千 token）：dev 默认 0（无云侧计费）；真实单价=云侧配置=实机放行项。
    llm_price_per_1k_in: float = 0.0
    llm_price_per_1k_out: float = 0.0

    # 生产数据源（M1+；M0 默认内存/文件系统档）。选定对应 vectorstore 档时由组合根注入其占位实现（缺值/缺驱动 fail-loud）。
    pg_dsn: str | None = None  # vectorstore=pgvector 回迁档的连接串（ADR-002；MilvusVectorStore 同口径的 PgVectorStore 消费）
    milvus_uri: str | None = None  # vectorstore=milvus 执行档连接（MilvusVectorStore 消费）
    # 对象库/队列企业档连接（M4-bu 真实现消费者；默认 None→默认 filesystem/pg_queue 档不读，选 s3/kafka 缺值即 fail-loud）。
    # 凭证不入本字段（T1：AWS/endpoint 密钥走 boto3 默认 env 链，此处仅桶名/端点/broker 等非密配置）。
    s3_bucket: str | None = None  # objectstore=s3 档桶名（S3ObjectStore 消费）
    s3_endpoint_url: str | None = None  # 兼容 S3 协议端点（MinIO 等；None=AWS 默认）
    kafka_broker: str | None = None  # mq=kafka 档 bootstrap.servers（KafkaMq 消费）

    # 目录批量入库（M4-ce 1b）的**允许根目录**：未配置＝入口 fail-loud（501/CLI 退出码 1），绝不"指哪读哪"。
    # 消费点＝`gateway/dir_ingest.py`（路径围栏单点执法：请求目录 resolve 后须 `is_relative_to` 本根，防 `../` 越界）。
    ingest_source_dir: str | None = None

    # 云侧凭证（T1：只从 env 读，禁字面量）
    cloud_llm_base_url: str | None = None
    cloud_llm_api_key: str | None = None
    cloud_embedding_base_url: str | None = None
    cloud_embedding_api_key: str | None = None
    cloud_rerank_base_url: str | None = None
    cloud_rerank_api_key: str | None = None
    # §10/R11 出网点白名单（逗号分隔主机名）：应用侧装配预检——选定云档时其 base_url 主机须在此集内（fail-fast）。
    # 网络层强制仍属部署侧放行项⑥（本机不伪造防火墙）；空=不启用预检（dev 档）。本字段是 egress 主机的唯一口径源。
    egress_allowlist_hosts: str = ""

    @property
    def shadow_breaker_set(self) -> set[str]:
        return {b.strip() for b in self.shadow_breakers.split(",") if b.strip()}

    @model_validator(mode="after")
    def _require_credentials_for_cloud(self) -> Settings:
        """E2 fail-fast：选了云档却没配凭证/端点 → 启动即失败，不静默降级。

        §10/R11 出网预检：已配 base_url 但主机不在 `egress_allowlist_hosts`（非空时）→ 装配期即拒，
        不等到运行期首次出网才暴雷。空白名单=dev 档不启用（承 extra=ignore 的既有 dev 默认路径）。
        """
        missing: list[str] = []
        if self.providers.llm == "cloud" and not (self.cloud_llm_base_url and self.cloud_llm_api_key):
            missing.append("CLOUD_LLM_BASE_URL/CLOUD_LLM_API_KEY")
        if self.providers.embedding == "cloud" and not (
            self.cloud_embedding_base_url and self.cloud_embedding_api_key
        ):
            missing.append("CLOUD_EMBEDDING_BASE_URL/CLOUD_EMBEDDING_API_KEY")
        if self.providers.rerank == "cloud" and not (
            self.cloud_rerank_base_url and self.cloud_rerank_api_key
        ):
            missing.append("CLOUD_RERANK_BASE_URL/CLOUD_RERANK_API_KEY")
        if missing:
            raise ValidationError.from_exception_data(
                title="装配表不完整",
                line_errors=[
                    {
                        "type": "missing",
                        "loc": ("cloud_credentials",),
                        "input": None,
                        "ctx": {"missing": ", ".join(missing)},
                    }
                ],
            )
        egress_offenders = self._egress_offenders()
        if egress_offenders:
            raise ValidationError.from_exception_data(
                title="出网白名单越界",
                line_errors=[
                    {
                        "type": "value_error",
                        "loc": ("egress_allowlist_hosts",),
                        "input": self.egress_allowlist_hosts,
                        "ctx": {"error": f"云档出网主机越界：{', '.join(egress_offenders)}"},
                    }
                ],
            )
        return self

    def _egress_offenders(self) -> list[str]:
        """选定云档的 base_url 主机不在白名单者（白名单空→一律视为合规，dev 档）。"""
        if not hosts_of(self.egress_allowlist_hosts):
            return []
        offenders: list[str] = []
        for selected, url in (
            (self.providers.llm == "cloud", self.cloud_llm_base_url),
            (self.providers.embedding == "cloud", self.cloud_embedding_base_url),
            (self.providers.rerank == "cloud", self.cloud_rerank_base_url),
        ):
            if selected and url and not egress_host_allowed(url, self.egress_allowlist_hosts):
                offenders.append(f"{host_of(url)} (allowlist={self.egress_allowlist_hosts})")
        return offenders



@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
