"""文档/canonical/切块契约（PRD §2 术语、FR-ING-6/7，F1/F5 收口件）。

canonical 正文持久化于 ObjectStorePort（键 `{doc_id}/{version}`），事件信封 CanonicalEvent 只带指针+元数据。
决议记录（ChunkDecision）= 判型与执行分离的载体：切块执行=对决议的确定性重放。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ChunkType = Literal["text", "table", "code"]
DocType = Literal["policy", "contract", "meeting", "wiki", "runbook", "other"]
PageState = Literal["digital", "scanned", "mixed"]  # FR-ING-8 三态探测


class Document(BaseModel):
    """源系统一份原始文件的版本化实体。"""

    doc_id: str
    version: int = 1
    source: str = "dev"
    title: str = ""
    domain: str = "default"  # 知识域
    doc_type: DocType = "other"
    acl_tags: list[str] = Field(default_factory=list)
    raw_text: str = ""
    page_state: PageState = "digital"
    language: str = "zh"


class PageSpan(BaseModel):
    """canonical 节点的位置锚点（引用溯源与邻居回取用）。"""

    page_no: int | None = None
    start_char: int | None = None
    end_char: int | None = None


class CanonicalNode(BaseModel):
    """canonical 文档的结构节点：标题层级/段落/表格/代码。"""

    node_id: str
    kind: Literal["heading", "paragraph", "table", "code", "list"] = "paragraph"
    text: str = ""
    level: int = 1
    breadcrumb: list[str] = Field(default_factory=list)
    span: PageSpan = Field(default_factory=PageSpan)
    children: list[CanonicalNode] = Field(default_factory=list)


class CanonicalDocument(BaseModel):
    """解析归一后的唯一文档表示（A/B 分区共享事实源，一级契约，R7）。"""

    doc_id: str
    version: int
    domain: str = "default"
    doc_type: DocType = "other"
    title: str = ""
    acl_tags: list[str] = Field(default_factory=list)
    root: list[CanonicalNode] = Field(default_factory=list)
    token_count: int = 0
    checksum: str = ""  # 正文哈希，供幂等/去重/对账

    def flatten(self) -> list[CanonicalNode]:
        out: list[CanonicalNode] = []

        def walk(nodes: list[CanonicalNode]) -> None:
            for node in nodes:
                out.append(node)
                walk(node.children)

        walk(self.root)
        return out


class CanonicalEvent(BaseModel):
    """canonical 事件信封（F1）：只带指针+元数据，正文走 ObjectStorePort。

    `doc_type`：把静态属性信号（制度/规范/runbook=候选）随信封带到 B 区 triage（FR-WIKI-4），
    免得 wiki 跨区回查 A 区 canonical（守跨区三禁）。默认 other=向后兼容，不改现有对账/幂等语义。
    `op`：canonical 流内的事件算子——`delete`=源文档删除的 tombstone（FR-WIKI-3 让 B 区把发布页标"依据已失效"），
    默认 `upsert`=常规编译。tombstone 无正文，store_key/checksum 留空默认值即可（不改 F1 指针语义）。
    """

    doc_id: str
    version: int
    domain: str
    acl_tags: list[str] = Field(default_factory=list)
    checksum: str = ""
    store_key: str = ""  # objectstore 指针
    token_count: int = 0
    origin: Literal["ingest", "reconcile_backfill"] = "ingest"
    doc_type: DocType = "other"
    op: Literal["upsert", "delete"] = "upsert"


class NodeDecision(BaseModel):
    """决议的一条节点段（D-1 的 node 级单元，ADR-010）：一段＝一个块的重放输入。

    `node_ids` 顺序即重放顺序；判型（chunk_type/confidence/decider）**按段落记**，
    因为同一份文档里 text 要聚合、table 要原子、code 要函数级——doc 级单值判型是结构病根。
    """

    node_ids: list[str] = Field(default_factory=list)
    chunk_type: ChunkType = "text"
    confidence: float = 1.0
    decider: Literal["rule", "layout_model", "llm", "default"] = "rule"


class ChunkDecision(BaseModel):
    """决议记录/决策表（D-1，v0.6.3；node 级重设计＝ADR-010）：三级漏斗的统一输出。

    doc 级只留 `profile`（本次决议主档＝归档键与降级标记位）与 `doc_token_count`（D-3 判例预算分母）；
    块型与判级逐段落在 `decisions` 上。随 doc_version 归档于 objectstore，切块执行＝对决议确定性重放。
    """

    profile: str
    decisions: list[NodeDecision] = Field(default_factory=list)
    doc_token_count: int = 0

    def with_decider(self, decider: Literal["rule", "layout_model", "llm", "default"]) -> ChunkDecision:
        """整份改写判级（D-3 降级件的人审位）：逐段落写，不留 doc 级第二套口径。"""
        updated = [d.model_copy(update={"decider": decider}) for d in self.decisions]
        return self.model_copy(update={"decisions": updated})


class Chunk(BaseModel):
    """检索最小单位（FR-ING-7 契约字段清单）。"""

    chunk_id: str
    doc_id: str
    doc_version: int
    seq: int
    text: str
    chunk_type: ChunkType = "text"
    breadcrumb: list[str] = Field(default_factory=list)
    page_no: int | None = None
    parent_chunk_id: str | None = None
    prev: str | None = None
    next: str | None = None
    acl_tags: list[str] = Field(default_factory=list)
    embedding_model: str = "none"
    input_token_limit: int = 8192
    token_count: int = 0
    authority: Literal["source", "wiki"] = "source"  # wiki 回写块=高权威（FR-WIKI-5）
    vector: list[float] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
