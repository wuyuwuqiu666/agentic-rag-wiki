"""gateway HTTP 门面（§5 line 93 "chat+wiki+admin **API**" 的 HTTP 半边，dev/演示档）。

CLI 半边（M4-bm）给的是"人可敲的命令"；本模块给出"浏览器可连的 HTTP"——同 `build_container()` 装配、
**1:1 委派既有已测 `App`/`Admin` façade**，本层零业务逻辑、不改冻结契约、不做认证。

诚实边界（勿误认企业级 API 已交付）：
- **认证＝放行档**：知识域有两条口径（M4-ca）——带 `role`＝**后端按 C 区角色聚合画像划分可见域**
  （`GET /api/scope` 读回、画像未建立即 409 且不给任何域）；不带 `role`＝退回调用侧声明的 `acl`（语义同 CLI `--acl`）。
  两条都只决定"以哪些域问"，pre-filter 一律在 `App` 内执法；`role` 仍是调用侧**自报**身份，本门面不实现 SSO/LDAP、
  不判"谁能问"，绝不把"能连上 HTTP"当成"有权限"。
- **装配＝dev/fake 档**：默认 `App` 走 `DevHashEmbedding`/`DevEchoLlm`，答案文本=模板回显（**接地、非模型质量档**，
  不得引用为幻觉率/质量结论）；演示 seed 灌入合成语料，仅供走通"答案↔出处联动"这条 UI 缝。
- **出处读回**：仅内存向量档支持按 `chunk_id` 读正文（`MemoryVectorStore.get_chunk`，非端口协议方法）；
  与 `search` 同一 pre-filter ACL 口径（给 id 也拉不到越权正文），换 Milvus/pgvector 档时该读回降级为不可用而非静默错值。
- **流式/背压/SSE 首 token<3s 真机数字**＝运行时放行档（§8.5 FR-CAP-2 / 放行项⑲），本层先给同步 JSON 闭环。
- **写侧入库 `POST /api/ingest`**：1:1 委派 `IngestPipeline.process_document`（B0/B4/DLP/F5 全在管线内执法，本层零旁路）；
  只接受**原始文本**，`acl_tags` 由调用侧声明为该文档可见域，真实按主体归因 ACL=鉴权放行档。
- **文件入库 `POST /api/ingest/file`**（M4-bz，M4-ce 1c 扩 OLE）：整份文件字节走 base64 JSON（非 multipart，免引新依赖）→
  `FileParserPort` 抽纯文本（`.docx`/`.pptx`=stdlib zipfile+xml、`.pdf`=pypdf 文本层、`.txt`/`.md`=解码、
  `.doc`/`.ppt`=先过容器真伪门再交本机 Office 转 OOXML）→同一摄取链。
  **诚实边界**：扫描版 PDF 无文本层→fail-loud 指路 OCR（放行项⑦）；`.xls`/`.xlsx` 不支持（缺表格正文抽取档，
  对齐口径属放行项⑧）；SharePoint/OA/网盘等**源连接器**（自动抓取+版面/OCR）仍属放行项㉒——
  本层只吃"已上传的单个文件字节"，不冒充真实源系统读取（`SourcePort` 见 M4-bi）。
- **变更入口 `POST /api/ingest/change` + 墓碑选单/恢复 `/api/ingest/{deletes,restore}`**（M4-ce）：把"入库之后"的
  下架与改可见域给到可达入口（破坏性动作须 `confirm=true`），删除/恢复的判定与落地全在管线内（B0/B1/`_purge`/墓碑账）。
  **诚实边界**：`domain_move` 执行档无消费点、`upsert` 须带正文，二者不经此入口（422 拒收而非静默收下不做事）；
  `acl_change` 兑现 FR-ING-5 四效应之②③④（M4-cd），效应①PG 权限行=放行项②——回执以 `effects_done`/`effects_pending`
  分列，并明写④的编译窗（源块即刻换域，但该页**已发布的权威块在下一轮编译前仍按旧受众可读**，
  撤下后须重新署名发布才回索引），不谎称"即刻改完受众"；
  `actor`＝署名回显、不入审计账（无贴切冻结 kind，专枚须 E3 ADR）；
  **批内逐条失败＝500 且把逐条账原样带出**（M4-cr／审计 #17 补录，`@app.exception_handler(ChangeApplyError)`）：
  其余条已生效的动作留在 `receipt.items` 上可指，本层不判定、不重试、也不改判"部分成功"（旧行为＝整批无回执＝部分效果无账）。
- **对账读回 `GET /api/ingest/reconcile`**（M4-ci）：B5 双册账（源块＋回写块）五个数 1:1 读回，CLI `admin reconcile` 同一口径。
  **诚实边界**：本入口**只读不传感**（`sense=False`）——刷新式读回若每次都记一次 B5 观察，硬断档下会把"看账"变成"拉闸"；
  读回也不动索引（见下条：改账是另一扇门）。
- **受众变更前值账读回 `GET /api/ingest/acl-history`**（M4-cq）：FR-ING-5 效应②的账面半边——逐版给「从哪些域收到哪些域、
  当时落在在册块（`inventory`）还是下架件（`retired`）还是待补正文（`pending_body`）」，CLI `admin acl-history` 同一口径、同一本账。
  **同样只读不传感**；三档落点不是失败：后两档索引当时没改成，但收紧已入账，恢复/补偿重放按最新域供答（不回落旧受众）。
  账＝进程内 stand-in（跨进程持久=放行项②），"谁改的"无贴切审计 kind（=放行项㉑），本面不冒充署名审计。
- **缺失块补偿 `POST /api/ingest/compensate`**（M4-cj）：FR-ING-4 的**写侧半边**可达入口（CLI `admin compensate` 同口径），
  1:1 委派 `IngestPipeline.compensate`——按清单指针从 objectstore 重拉正文、同 chunk_id 幂等重放，判定全在管线内。
  **诚实边界**：须 `confirm=true`（批量写侧动作，同 `/api/ingest/{restore,dir}` 一道门）；**两道门都在写侧之前**
  （先 B0 总闸、再 B5 单类闸；B0 半边＝M4-cv／审计 #14——此前本入口只判 B5，而 B0 的门住在 `process_document` 里，
  冻结期在"这一批没有正文可重放"时照样 200＋`backfilled:0`）；一次动作只传感一次，硬断档下整批 503、零部分效果
  （不给"改了半截没账"的态）；补的是源块，回写块缺账不经此补（处置＝B 区重编译），
  正文真丢不臆造、如实留 `missing_after`；**执行档故意不给前端按钮**（批量修复动作的确认门留在命令面/接口面，不在页面做成"一键修复"）。
- **影子索引重建 `POST /api/ingest/reindex` ＋ 别名切回 `POST /api/ingest/alias/revert`**（M4-cm）：FR-ING-3 与 §10
  「一键切回旧别名」高危回滚点的可达面（CLI `admin reindex` / `admin alias-revert` 同口径），
  1:1 委派 `IngestPipeline.shadow_rebuild`/`revert_alias`——建影子、原子切换、失败即时回滚、别名悬空 fail-loud 全在
  `service/reindex` 内执法，本层不选别名对、不挑重建范围。
  **诚实边界**：须 `confirm=true` 且 **B0 总闸判门在全量重放/触库之前**（本刀同时补齐这条门：两条路径此前不判 B0，
  拉闸期照样能把全量正文重放进两路索引＝"冻结全部写侧"被最大的写路径绕过）；dev 单 store 无 collection 物理隔离，
  切换期查询错误率 <0.1% 的数值验收＝放行项⑪、真·collection 别名与 72h 保留窗＋切回审计＝放行项②，本层不谎称已测得。
- **目录批量入库 `POST /api/ingest/dir`**（M4-ce 1b）：整目录走 `SourcePort`→`IngestPipeline.pull`（FR-ING-1 存量遍历），
  读盘范围由 `APP_INGEST_SOURCE_DIR` 允许根目录围栏（未配 501／越界 403／非目录 422／覆盖系统产物目录 422，
  HTTP 与 CLI 共用单点口径）。
  **诚实边界**：位点＝进程内指纹账（重启即当作新批，跨进程位点=放行项②）；内容变过的文件只列进回执 `changed`
  并指路"先下架再重跑"，本入口不表达"文件改了"（执行档 upsert 会重放旧正文）；逐文档元数据/增量调度=真实连接器放行项㉒。
- **B 区人审发布 `/api/wiki/{compile,pages,review}`**（M4-cb）：把"入库之后、成为权威页之前"那段人审链路给到可点入口
  （编译→草稿→提交人审→署名通过/打回），门与状态迁移仍由 `wiki/service/review.py` 执法（本层只翻 HTTP 码）。
  **诚实边界**：`reviewer`＝调用侧自报署名（非认证、不判"谁有审核权"），作者≠审阅人的跨人审批分离＝E9 治理放行档；
  页清单/审阅动作的可见域与问答同一 `can_read_field` 闸（越权页不列出，动作 404 不给存在性）。

依赖：FastAPI/uvicorn 属 `pyproject` 可选 extra `[http]`（MIT），不进单机核心依赖、不扰动 `deps_governance` 核心 3 项。
"""

from __future__ import annotations

import base64
import contextlib
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.partitions.ingest.runtime.pipeline import ChangeApplyError
from src.partitions.memory.service.domain_scope import visible_domains
from src.partitions.memory.service.portrait_policy import aggregation_key, can_read_field
from src.partitions.wiki.service.review import ReviewError
from src.platform.gateway.composition import Container, build_container
from src.platform.gateway.dir_ingest import (
    DirIngestNotConfigured,
    DirOutsideRoot,
    IngestRootOverlapsArtifact,
    NotAnIngestDirectory,
    run_dir_ingest,
)
from src.platform.providers.errors import BreakerTripped
from src.platform.types import ChangeEvent, Document, PortraitAgg, WikiPage
from src.platform.types.document import ChunkType, DocType

# —— 外部输入 schema（T2：浏览器请求不可信，先进 pydantic 校验，再进 façade）——


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=512)
    acl: list[str] = Field(default_factory=list)  # 已鉴权主体的权限域（非认证，见模块头）
    role: str | None = Field(default=None, min_length=1, max_length=64)  # 角色标识（调用侧声明，非认证）
    trace_id: str | None = Field(default=None, max_length=128)

    def resolve_acl(self, portraits: list[PortraitAgg]) -> list[str]:
        """知识域口径：见 `_reader_domains`（全门面唯一解析点）。两路拿到的域都在 `App` 内以同一 pre-filter
        口径执法——本层只决定"以哪些域问"，不实现"怎么过滤"。
        """
        return _reader_domains(self.role, self.acl, portraits)


class SubjectScope(BaseModel):
    """主体可见域读回（前端只显、不可改）：知识域由后端按画像划分，取代手工勾选。"""

    role: str
    domains: list[str]


class IngestRequest(BaseModel):
    """知识入库请求（dev/演示档）：调用侧提供 `acl_tags` = 该文档的可见域，post-ingest 检索时由 pre-filter 执法。

    诚实边界：这不是"上传后仅上传者可见"——真实 ACL 归因来自已鉴权主体（放行档=SSO/LDAP 鉴权层）；
    本层只把用户填写的域当作元数据带到 Document.acl_tags，与 seed 语料同一口径。
    """

    doc_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    version: int = Field(default=1, ge=1, le=1_000_000)
    title: str = Field(default="", max_length=200)
    domain: str = Field(default="default", max_length=32)
    doc_type: DocType = "other"
    acl_tags: list[str] = Field(default_factory=list)
    raw_text: str = Field(min_length=1, max_length=200_000)


class IngestFileRequest(BaseModel):
    """文件入库请求（dev/演示档）：`content_b64`=整份文件字节的 base64（非 multipart，避免引新依赖）。

    门面先按 `filename` 扩展名交 `FileParserPort` 抽正文＋页态（docx/pptx=stdlib、pdf=逐页文本层＋无字页交本机
    OCR、txt/md=解码、doc/ppt=容器真伪门 + 本机 Office 转 OOXML），
    再把抽出的文本喂进与 `POST /api/ingest` 同一 `IngestPipeline`（B0/B4/DLP/F5 全在管线内、零旁路）。
    诚实边界：扫描版 PDF 的识别供给＝`ScanOcrPort`（执行档 RapidOCR＋pypdfium2，M4-cf 收口放行项⑦；
    该供给不在位即 422 指路 `pip install 'agentic-rag-wiki[ocr]'`，认出字的页如实标 `page_state=scanned/mixed`，
    **认不出字的页不进正文**并列进回执 `failed_pages`）；legacy .doc/.ppt 需本机 Office 转换（COM 不在位即 422 指路外部转换器＝㉒）；
    .xls/.xlsx 无表格正文抽取档→415/422（对齐表格原子化口径属放行项⑧）。
    """

    doc_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    version: int = Field(default=1, ge=1, le=1_000_000)
    title: str = Field(default="", max_length=200)
    domain: str = Field(default="default", max_length=32)
    doc_type: DocType = "other"
    acl_tags: list[str] = Field(default_factory=list)
    filename: str = Field(min_length=1, max_length=255)
    content_b64: str = Field(min_length=1, max_length=8_000_000)  # ~6MB 原始文件上限（base64 4/3 膨胀）


class IngestChangeRequest(BaseModel):
    """已入库文档的变更请求（FR-ING-2/5 的 HTTP 半边）：下架（delete）、改可见域（acl_change）。

    与 `POST /api/ingest` 互补——那条表达"源系统有新正文"，本条表达"已入库文档发生了什么变化"。
    1:1 委派 `IngestPipeline.apply_changes`：B0 写侧总闸、B1 批量删除占比、`_purge`（向量+词法两路同清 +
    按域清语义缓存）、§10 墓碑记账、FR-WIKI-3 tombstone 广播全在管线内执法，本层不判定也不绕过。

    只收当前**有真实执行语义**的两种 kind，其余一律 422 拒收而非"收下但什么都不做"（静默失败比拒绝更危险）：
    - `upsert` 需正文 → 走 `/api/ingest`；
    - `domain_move` 执行档尚无消费点：换域不只是重写块 ACL，还要连同 B 区页归属（`page_id` 首段即域）整域迁移＋
      两路索引重建，此道未实现（`ChangeKind` 属 E3 冻结契约，移除须 ADR），故暂不经此入口接受。
    诚实边界：`actor`＝调用侧自报署名（非认证，同 `reviewer` 口径），"谁能下架"归鉴权放行档；
    `acl_change` 兑现 FR-ING-5 四效应之②③④（M4-cd：源块 ACL 重写走两路索引+清单、按域清缓存、受影响权威页经
    fan-out 在下一轮编译标脏撤块），效应①PG 权限行=放行项②（真实双存储）——回执以 `effects_done`/`effects_pending`
    分列。**收紧分两段且如实写出**：源块即刻、该页已发布的权威块要到下一轮编译才撤，此窗内旧受众仍可读该块
    （测试锁＝tests/contract/test_writeback_lifecycle.py 第⑤条），故不写"即刻改完受众"。
    """

    doc_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    kind: Literal["delete", "acl_change"]
    version: int | None = Field(default=None, ge=1, le=1_000_000)  # delete：缺省=整 doc 全版本
    actor: str = Field(min_length=1, max_length=64)
    confirm: Literal[True]  # 破坏性动作须显式二次确认（前端勾选），不接受默认值
    acl_before: list[str] = Field(default_factory=list, max_length=64)
    acl_after: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> IngestChangeRequest:
        if self.kind == "acl_change" and not self.acl_after:
            raise ValueError("acl_change 必须给出 acl_after（改成「对全部域不可见」也应显式声明，不接受缺省）")
        return self


class DeleteRestoreRequest(BaseModel):
    """墓碑恢复请求（§10 软删除的回滚点）：`doc_id` 给定=只回滚该文档，缺省=全量待恢复墓碑。

    1:1 委派 `IngestPipeline.restore_deletes`——正文仍在 objectstore 且未过保留期才谈得上找回，
    过窗/正文真丢管线如实计入不可恢复，本层不承诺"一定能找回"。
    """

    doc_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    actor: str = Field(min_length=1, max_length=64)
    confirm: Literal[True]


class CompensateRequest(BaseModel):
    """B5 缺失块补偿请求（FR-ING-4 的写侧半边，M4-cj）：整批重放，无逐条选项。

    1:1 委派 `IngestPipeline.compensate`：按清单指针从 objectstore 重拉正文、同 chunk_id 幂等重放入库全在管线内执法，
    本层不选块、不判定、也不提供"补哪几条"的参数——补偿的口径是**账上缺哪块补哪块**，让人来挑就变成人肉臆造。
    `confirm` 须显式为 true：这是批量写侧动作（改两路索引 + 落报告），与 `/api/ingest/restore`、`/api/ingest/dir` 同一道门。
    诚实边界：`actor` 署名口径同 `/api/ingest/change`（回显、不入审计账）；谁能点=鉴权放行档。
    """

    actor: str = Field(min_length=1, max_length=64)
    confirm: Literal[True]


class ReindexRequest(BaseModel):
    """影子索引全量重建请求（FR-ING-3 的写侧半边，M4-cm）：无别名参数、无范围参数。

    1:1 委派 `IngestPipeline.shadow_rebuild`：影子全量重放→别名原子切换→切换失败即时回滚全在管线与
    `service/reindex` 内执法，本层不选别名、不挑范围——别名对是索引侧的固定口径（执行档单 store），
    让人来挑就变成把查询指向切到一个没人服务的位置。
    `confirm` 须显式为 true：全量重放＝最重的一次写侧动作，B0 总闸判门在其**之前**（冻结期整批 503、零部分效果）。
    诚实边界：`actor` 署名口径同 `/api/ingest/change`（回显、不入审计账）；"切换期查询错误率 <0.1%" 的数值验收
    须真实 PG+Milvus 双 collection＝**放行项⑪**（②只覆盖双存储与别名/审计本体，数值验收另册），本入口只兑现控制流，不谎称已测得该数。
    """

    actor: str = Field(min_length=1, max_length=64)
    confirm: Literal[True]


class AliasRevertRequest(BaseModel):
    """搜索别名整域切回请求（§10 高危回滚点「一键切回旧别名」，M4-cm）：1:1 委派 `IngestPipeline.revert_alias`。

    与上条的分工＝自动 vs 手动：`shadow_rebuild` 只在**切换失败**那一刻即时回滚，本条用于切换成功、事后人工判定
    新索引劣化（相关性/错误率回退）时主动整域退回保留窗内的上一版本。同名 422 在边界拦下、不触库（同名表达不了"切回"）。
    诚实边界：dev 单 store 的别名不影响查询面（search 直取逻辑集），此处兑现的是运维入口与 store 抛错不静默的语义；
    真实 collection 别名与操作审计随双存储落地＝放行项②。`actor` 署名口径同上。
    """

    actor: str = Field(min_length=1, max_length=64)
    from_alias: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    to_alias: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    confirm: Literal[True]

    @model_validator(mode="after")
    def _must_differ(self) -> AliasRevertRequest:
        if self.from_alias == self.to_alias:
            raise ValueError("from_alias 与 to_alias 必须不同（同名无法表达切回）")
        return self


class IngestDirRequest(BaseModel):
    """目录批量入库请求（M4-ce 1b，FR-ING-1 存量遍历的 HTTP 半边）。

    `directory`＝**相对于允许根目录**的路径（根由 `APP_INGEST_SOURCE_DIR` 配置，缺配即 501）；
    空串＝根目录本身。不接受绝对路径"指哪读哪"——越界（含 `..`/符号链接逃逸）在围栏处 403。
    `domain`/`doc_type`/`acl_tags`＝本批文档的元数据缺省（真实源系统的逐文档元数据=放行项㉒）；
    `confirm` 须显式为 true：一批文件=一次写侧批量动作，须调用侧确认"确实要整目录入库"，不接受默认放行。
    诚实边界：`actor` 署名口径同 `/api/ingest/change`（回显、不入审计账）；解析失败/超限/撞 id/内容变过的
    文件**不静默丢弃也不阻断整批**，逐条列在回执 `skipped`/`conflicts`/`changed`/`unchanged`。
    """

    directory: str = Field(default="", max_length=512)
    domain: str = Field(default="default", max_length=32)
    doc_type: DocType = "other"
    acl_tags: list[str] = Field(default_factory=list, max_length=64)
    actor: str = Field(min_length=1, max_length=64)
    confirm: Literal[True]


class SourceView(BaseModel):
    """出处卡片读回视图（右侧联动面板用）：只暴露展示字段，不回传向量。"""

    chunk_id: str
    doc_id: str
    doc_version: int
    text: str
    authority: Literal["source", "wiki"]
    breadcrumb: list[str] = Field(default_factory=list)
    acl_tags: list[str] = Field(default_factory=list)
    chunk_type: ChunkType = "text"  # D-2 块型：右侧联动面板据此分排（表格/代码不按散文渲染）
    parent_chunk_id: str | None = None  # 表格退化行组的父块（FR-ING-7/ADR-010），供"同表其他行组"提示


class WikiSectionView(BaseModel):
    heading: str
    body: str = ""
    source_chunk_ids: list[str] = Field(default_factory=list)  # FR-WIKI-3 溯源不变（审阅时可见依据）


class WikiPageView(BaseModel):
    """B 区页视图（审核台用）：状态机字段 + 可核对正文；只在调用者可见域内返回（见 `/api/wiki/pages`）。"""

    page_id: str
    domain: str
    title: str
    status: str
    acl_tags: list[str] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)
    reviewer: str = ""
    triage_reason: str = ""
    version: int = 1
    sections: list[WikiSectionView] = Field(default_factory=list)


class WikiReviewRequest(BaseModel):
    """人审动作（FR-WIKI-1）：`submit`=草稿提交人审、`approve`=署名通过并发布回写、`reject`=打回。

    本层不判迁移合法性也不判署名——那条门在 `wiki/service/review.py` 状态机（唯一执法点），
    门面只把不可信输入（T2 schema 校验过的）交进去，并把 `ReviewError` 翻成 409。
    """

    page_id: str = Field(min_length=1, max_length=200)
    action: Literal["submit", "approve", "reject"]
    reviewer: str = Field(default="", max_length=64)
    role: str | None = Field(default=None, min_length=1, max_length=64)
    acl: list[str] = Field(default_factory=list)


def _reader_domains(role: str | None, acl: list[str], portraits: list[PortraitAgg]) -> list[str]:
    """可见域单一口径（M4-ca）：`role` 在位＝后端按 C 区角色聚合画像划分（画像未建立即 fail-loud、不放行）；
    `role` 缺位＝退回调用侧声明的 `acl`（承 M4-bw 的 dev 兜底，语义同 CLI `--acl`）。

    `/api/ask`·`/api/source`·`/api/wiki/*` 全部经这一处解析，防止"某条入口少判一次"造成口径漂移。
    """
    if role is None:
        return acl
    return visible_domains(role, portraits)


def _page_view(page: WikiPage) -> WikiPageView:
    return WikiPageView(
        page_id=page.page_id,
        domain=page.domain,
        title=page.title,
        status=page.status,
        acl_tags=list(page.acl_tags),
        source_doc_ids=list(page.source_doc_ids),
        reviewer=page.reviewer,
        triage_reason=page.triage_reason,
        version=page.version,
        sections=[
            WikiSectionView(heading=s.heading, body=s.body, source_chunk_ids=list(s.source_chunk_ids)) for s in page.sections
        ],
    )


def _create_app(container: Container | None) -> Any:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    ctr = container if container is not None else build_container()
    seed_state = seed_demo(ctr)  # 冻结期如实 skipped，不炸启动（拉闸冻写侧、保读侧）
    app = FastAPI(title="Agentic RAG + LLM wiki（dev/演示档）", version="0.1")
    # 演示档：前端与门面同源或本机 dev server，放开 localhost CORS（非生产策略）。
    app.add_middleware(
        CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?", allow_methods=["*"], allow_headers=["*"]
    )

    def _domains(role: str | None, acl: list[str]) -> list[str]:
        """可见域解析（唯一口径 `_reader_domains`）；画像未建立/角色标识非法＝409 且**不给任何域**。"""
        try:
            return _reader_domains(role, acl, ctr.portraits.all())
        except ValueError as exc:  # PortraitNotReady 及非法角色标识
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.exception_handler(BreakerTripped)
    def _breaker_tripped(_request: Any, exc: BreakerTripped) -> Any:
        """写侧被熔断闸拦下：给调用方**是哪一类闸**的可读原因，不外抛栈；读侧不受此门影响。

        breaker id 一律取自判定本身，不写死：把 B1/B3/B4/B5 的拦停都说成"（B0）"会让人去复位一条
        根本没触发的总闸（治复位疲劳正是本档的既有口径），而 runbook 是按类分的。
        """
        from fastapi.responses import JSONResponse

        reason = getattr(exc.decision, "reason", "")
        breaker = getattr(exc.decision, "breaker", "B0")
        frozen = breaker == "B0"  # 只有总闸才是"拉闸冻结全部写侧"；单类闸拦的是这一次动作
        head = "写侧已拉闸（B0）" if frozen else f"写侧动作被熔断闸拦下（{breaker}）"
        return JSONResponse(
            status_code=503,
            content={"detail": f"{head}：{reason}——按 {breaker} 的 runbook 处置后双人复位"},
        )

    @app.exception_handler(ChangeApplyError)
    def _change_apply_error(_request: Any, exc: ChangeApplyError) -> Any:
        """变更批里有逐条失败：500 并**把逐条账原样带出**（M4-cr／审计 #17 补录）。

        为什么不写成各路由里的 `try/except`＝本异常可从 `/api/ingest/change` 与 `/api/ingest/dir`（经 `pull`）
        两处冒出，在每处各写一遍翻译＝同一事实两种算法；与 `BreakerTripped` 同一处置：一处渲染、口径唯一。
        本层不判定、不重试、也不改判"部分成功"＝管线的账怎么写就怎么给（`receipt["items"]` 逐条给
        批内序号/结果/原因，运维据此列处置清单）。
        """
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=500, content={"detail": str(exc), "receipt": exc.receipt})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        # `seed`＝演示语料这一次写侧动作的真实结果（M4-cs）：拉闸期起门面会 skipped 而非崩，读侧仍在服务。
        return {"status": "ok", "assembly": "dev/fake", "quality": "echo-not-model", "seed": seed_state}

    @app.get("/api/scope")
    def scope(role: str = Query(min_length=1, max_length=64)) -> dict[str, Any]:
        """主体可见域读回：知识域由后端按 C 区聚合画像划分，前端只显不可改。

        画像未建立＝409 且**不给任何域**（冷启动不放大权限，非"暂时全放行"）。
        """
        return SubjectScope(role=role, domains=_domains(role, [])).model_dump()

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        # 知识域口径：role 在位＝后端按画像划分（无画像即 409，绝不静默给全域）；role 缺位＝退回调用侧声明 acl。
        # 无论哪条，过滤都在 App 内的 pre-filter 执法，本层不实现权限判定。
        try:
            user_acl = req.resolve_acl(ctr.portraits.all())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        answer = ctr.app.answer(req.question, user_acl=user_acl, trace_id=req.trace_id)
        return answer.model_dump()

    @app.post("/api/ingest")
    def ingest(req: IngestRequest) -> dict[str, Any]:
        # 1:1 委派摄取链（本层零业务逻辑）：canonicalize→决议→切块→准入→embed→双写→fan-out 全在 IngestPipeline 内，
        # B0 写侧总闸、B4 注入探测、DLP 隔离、F5 截断告警等均不旁路；门面只把浏览器不可信输入喂进已测管线。
        if ctr.ingest is None:  # pragma: no cover — 组合根恒装配摄取链
            raise HTTPException(status_code=501, detail="摄取链未装配")
        event = ctr.ingest.process_document(
            Document(
                doc_id=req.doc_id,
                version=req.version,
                title=req.title,
                domain=req.domain,
                doc_type=req.doc_type,
                acl_tags=req.acl_tags,
                raw_text=req.raw_text,
            )
        )
        return event.model_dump()

    @app.post("/api/ingest/file")
    def ingest_file(req: IngestFileRequest) -> dict[str, Any]:
        # 文件→文本→同一摄取链：解析委派 FileParserPort（本层零解析逻辑），抽取后的文本走与纯文本端点一致的管线。
        if ctr.ingest is None:  # pragma: no cover — 组合根恒装配摄取链
            raise HTTPException(status_code=501, detail="摄取链未装配")
        if not ctr.fileparser.supported(req.filename):
            raise HTTPException(status_code=415, detail=f"暂不支持的文件类型：{req.filename}")
        try:
            data = base64.b64decode(req.content_b64, validate=True)
        except Exception as exc:  # 非法 base64=客户端错误，不进解析
            raise HTTPException(status_code=422, detail="content_b64 非法（不是有效 base64）") from exc
        try:
            parsed = ctr.fileparser.parse(req.filename, data)  # FileParseError(ValueError)→422，绝不吞成空文本入库
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"文件解析失败：{exc}") from exc
        text = parsed.text
        if not text.strip():
            raise HTTPException(status_code=422, detail="解析结果为空文本（疑扫描件或空文档）")
        if len(text) > 200_000:
            raise HTTPException(status_code=413, detail="解析文本超出单文档上限（200k 字符）")
        event = ctr.ingest.process_document(
            Document(
                doc_id=req.doc_id,
                version=req.version,
                title=req.title or req.filename,
                domain=req.domain,
                doc_type=req.doc_type,
                acl_tags=req.acl_tags,
                raw_text=text,
                page_state=parsed.page_state,  # 页态随正文一起落账：管线的 OCR 门禁与质量门据此生效（FR-ING-8）
            )
        )
        out = event.model_dump()
        out["source_file"] = req.filename
        out["extracted_chars"] = len(text)
        out["page_state"] = parsed.page_state
        out["ocr_pages"] = list(parsed.ocr_pages)  # 本次由 OCR 供给认出的页（回执可核，不藏半个真相）
        out["failed_pages"] = list(parsed.failed_pages)  # 认不出字而被排除的页＝失败页不进索引的账面
        return out

    @app.post("/api/ingest/change")
    def ingest_change(req: IngestChangeRequest) -> dict[str, Any]:
        """已入库文档的变更入口（下架 / 改可见域）：1:1 委派 `IngestPipeline.apply_changes`，本层零判定。

        删除=破坏性动作，`confirm=true` 方受理；落地仍走管线内的 `_purge`（向量+词法两路同清 + 按域清缓存）
        与 §10 墓碑记账，B0 拉闸期以 503 冻写、B1 批量删除占比闸不因"只发一条"而旁路（占比按批算）。
        诚实边界：`actor` 仅为请求署名回显——执行档"下架"没有贴切的冻结审计 kind（`AuditEvent.kind` 九成员属
        E3 契约，专枚删除 kind 须人工 ADR + `contract_compat --update`），故此处不挪用 `quarantine`（DLP 语义）
        冒充"已记审计账"，署名不跨进程留存=在册缺口。
        """
        if ctr.ingest is None:  # pragma: no cover — 组合根恒装配摄取链
            raise HTTPException(status_code=501, detail="摄取链未装配")
        event = ChangeEvent(
            doc_id=req.doc_id,
            kind=req.kind,
            version=req.version,
            acl_tags_before=list(req.acl_before),
            acl_tags_after=list(req.acl_after),
            ts=time.time(),
        )
        stats = ctr.ingest.apply_changes([event])
        out: dict[str, Any] = {
            "kind": req.kind,
            "doc_id": req.doc_id,
            "actor": req.actor,
            # 与 GET /api/ingest/deletes、restore 回执同一口径（M4-cn⑧）：窗内**且**正文仍在才叫"进可恢复账"
            "recoverable": sum(
                1 for t in ctr.ingest.pending_deletes() if t["doc_id"] == req.doc_id and t["recoverable"] and t["has_body"]
            ),
            **stats,
        }
        if req.kind == "delete":
            out["hint"] = (
                "已下架：两路索引与语义缓存同清，问答不再命中。保留期内可经 POST /api/ingest/restore 回滚"
                if out["recoverable"]
                else "已下架：本条未进可恢复账（无登记信封或正文指针缺失），不做恢复承诺"
            )
        else:
            out["effects_done"] = [
                "源块 ACL 重写（两路索引 + 清单，即刻）",
                "语义缓存按前后域并集清",
                "源块重放已 fan-out→受影响权威页将在下一轮编译标脏并撤块",
                "受众前值已入账（GET /api/ingest/acl-history 可读回 from→to）",
            ]
            out["effects_pending"] = ["PG 权限行（效应①，真实双存储=放行项②）"]
            out["pending_acl_blocks"] = stats.get("acl_pending", 0)
            out["retired_acl_docs"] = stats.get("acl_retired", 0)
            out["hint"] = (
                "源块的两路索引与清单已按新域重写、语义缓存已清，问答即刻按新域过滤。"
                "但该源若已发布过权威页，那张已发布的权威块仍按旧受众可读"
                "要到下一轮编译（POST /api/wiki/compile 或界面「跑一轮编译」）才标脏撤块；"
                "撤下后重合成的块须重新署名发布才回索引＝受众变更由人签字，不自动复权。"
                "要即刻关窗＝改完域马上跑一轮编译。"
                "若该文档当时已下架或正文缺失（看 `retired_acl_docs`/`pending_acl_blocks`），索引那一路改不到，"
                "但收紧**已逐版落进受众前值账**：恢复或补偿重放按最新域供答，不会回落旧受众。"
            )
        return out

    @app.get("/api/ingest/deletes")
    def ingest_deletes() -> dict[str, Any]:
        """删除墓碑选单（§10 保留期内可恢复的可读面）：逐条给可恢复性、剩余窗口秒数、正文是否仍在。

        过窗与正文缺失一律如实标不可恢复（不臆造"还能一键找回"）；跨进程重启后的账实持久=PG 放行项②。

        汇总三个数与恢复作业回执同一口径（M4-cn⑧）：`recoverable`＝窗内**且**正文仍在（=POST /api/ingest/restore 的
        `recoverable`），`body_missing`＝窗内但正文已丢，`expired`＝超窗；故 `items` 窗内数＝recoverable+body_missing。
        """
        if ctr.ingest is None:  # pragma: no cover
            raise HTTPException(status_code=501, detail="摄取链未装配")
        now = time.time()
        items = ctr.ingest.pending_deletes(now=now)
        return {
            "now": now,
            "items": items,
            "recoverable": sum(1 for t in items if t["recoverable"] and t["has_body"]),
            "body_missing": sum(1 for t in items if t["recoverable"] and not t["has_body"]),
            "expired": sum(1 for t in items if not t["recoverable"]),
        }

    @app.get("/api/ingest/reconcile")
    def ingest_reconcile() -> dict[str, Any]:
        """B5 对账读回（FR-ING-4/§8.1 的可达入口，M4-ci）：1:1 委派 `IngestPipeline.reconcile(sense=False)`。

        五个数全部由管线同一册算出，本层零判定、零新口径。**只读且不传感**（`sense=False`）：读回会被反复刷新，
        而 B5 硬断档下每次越阈都记一条未复位触发、连续达阈升 B0 冻结写侧——"看一眼账"不得变成"自己把系统拉闸"。
        传感仍在写侧动作的既有调用点（恢复/补偿后自跑对账）与逐窗采样作业（真实调度=放行档）。
        处置指路（读回不代做）：`missing` 里属源块的那份＝可重拉正文的补偿对象（写侧入口＝`POST /api/ingest/compensate`，M4-cj）；
        属回写块的那份**不经补偿**——A 区没有页正文，凭 canonical 重放造不出那张权威页，处置＝B 区重编译→人审→重发布；
        `extra`＝真孤儿（两册皆无），只报告不自动删（删除须过 B1、交变更流处置）。
        """
        if ctr.ingest is None:  # pragma: no cover
            raise HTTPException(status_code=501, detail="摄取链未装配")
        rec = ctr.ingest.reconcile(sense=False)
        return rec | {
            "hint": (
                "账实相符＝missing 与 extra 皆 0。missing≠0 时先分册：源块缺＝可按清单指针重拉正文（`POST /api/ingest/compensate`）；"
                "回写块缺＝该权威页需重编译并重新署名发布，补偿作业补不了它。"
                "extra≠0＝两册清单皆无的真孤儿块，只报告不自动删（删除是写侧动作，须过 B1）。"
                "本读回不记熔断观察，故不构成 B5 触发；背离债的传感在补偿/恢复等写侧动作里。"
            )
        }

    @app.get("/api/ingest/acl-history")
    def ingest_acl_history(doc_id: str, version: int | None = None) -> dict[str, Any]:
        """受众变更前值账读回（FR-ING-5 效应②的账面半边，M4-cq）：1:1 委派 `IngestPipeline.acl_history`，本层零判定。

        存在的理由＝收紧一旦只存在于"当前块标量"里，就没人能回答"这页凭什么从 fin 收到 hr、改的时候它是不是已下架"。
        与 `GET /api/ingest/reconcile` 同一条读侧纪律：**只读不传感**（不记熔断观察、不动索引、不改账），
        刷新式看账不得变成故障源；也不给写侧参数（看账不是改账，改受众是 `POST /api/ingest/change` 那扇门）。
        `applied_to` 三档读法见处置页 `ingest_retire.md`：`retired`/`pending_body` 都不是失败——收紧已入账，
        恢复或补偿重放时按新域供答，不会回落旧受众。
        诚实边界：账＝进程内 stand-in（重启即空，跨进程持久＝放行项②）；"谁改的"不落审计账——
        `AuditEvent.kind` 是 E3 冻结 Literal、无贴切成员（专枚 kind 须人工 ADR＝放行项㉑），本面不冒充署名审计。
        """
        if ctr.ingest is None:  # pragma: no cover — 组合根恒装配摄取链
            raise HTTPException(status_code=501, detail="摄取链未装配")
        entries = ctr.ingest.acl_history(doc_id, version)
        return {
            "doc_id": doc_id,
            "version": version,
            "entries": entries,
            "hint": (
                "空列表＝该文档从未被 acl_change 改过受众（不是「没记上」）。"
                "applied_to=inventory＝在册块已两路改写；retired＝改的是账，索引当时没有它，恢复即按最新域；"
                "pending_body＝正文指针缺失当时没改成索引，补回正文后重放按最新域。"
                "后两档都不构成 B5 背离债的变化，本读回只如实报账。"
            ),
        }

    @app.post("/api/ingest/compensate")
    def ingest_compensate(req: CompensateRequest) -> dict[str, Any]:
        """缺失块补偿（FR-ING-4 写侧半边，M4-cj）：1:1 委派 `IngestPipeline.compensate`，本层零判定、零挑块参数。

        与上条 `GET` 的分工就是本档的读/写纪律：GET 只给数不动账（且只读不传感），POST 才动索引——
        把补偿做成 `GET` 的副作用＝"看一眼账"顺手把系统改了，故 `GET` 不是伪装的"一键修复"，本条也不给前端按钮（在册）。
        一次动作只传感一次，且**两道门都在写侧之前**：先 B0 总闸（M4-cv／审计 #14——补的正是"此前只判 B5、
        冻结期在这一批没有正文可重放时照样给 200 成功形状"那条背离），再 B5 单类闸（缺块 ≥ 阈）；
        硬断档下整批以 503 结束、零部分效果——
        不出现"索引已改一半、报告没落、回执没给"的无账态（同 M4-ce 1a/1b 与 M4-cb 的事务口径）。
        诚实边界：补的是**源块**（凭清单指针从 objectstore 重拉正文，同 chunk_id 幂等重放，非新内容）；
        正文真丢=不臆造、留在 `missing_after` 里如实报账；回写块缺账不经本作业补（处置＝B 区重编译→人审→重发布）；
        孤儿块只进报告不自动删。`report`＝本轮报告路径，`write_report=False` 的静默档只在进程内作业可用。
        """
        if ctr.ingest is None:  # pragma: no cover — 组合根恒装配摄取链
            raise HTTPException(status_code=501, detail="摄取链未装配")
        return {"actor": req.actor, **ctr.ingest.compensate(now=time.time())}

    @app.post("/api/ingest/reindex")
    def ingest_reindex(req: ReindexRequest) -> dict[str, Any]:
        """影子索引全量重建（FR-ING-3 写侧半边，M4-cm）：1:1 委派 `IngestPipeline.shadow_rebuild`，本层零判定。

        控制流在 `service/reindex.run_shadow_reindex`：先全量建影子→原子切别名→切换抛错则回滚旧别名→
        回滚也失败＝别名悬空，按 NFR-8 fail-loud 抛 `ProviderError`（500，绝不静默返回"看起来切完了"）。
        四个数按 `ReindexOutcome` 原样回显，本层不加解释也不改判：`applied=false` **不等于**系统坏了——
        回滚成功（`rolled_back=true`）时旧别名仍在服务，这正是设计要的失败形态。
        诚实边界：B0 冻结期整批 503 且零部分效果（判门在全量重放之前）；真实 collection 物理隔离＝放行项②、切换期
        查询错误率 <0.1% 的数值验收＝放行项⑪；切成功后要整域退回旧版本＝下条 `POST /api/ingest/alias/revert`（手动回滚是另一扇门）。
        """
        if ctr.ingest is None:  # pragma: no cover — 组合根恒装配摄取链
            raise HTTPException(status_code=501, detail="摄取链未装配")
        outcome = ctr.ingest.shadow_rebuild()
        return {
            "actor": req.actor,
            "applied": outcome.applied,
            "rolled_back": outcome.rolled_back,
            "active_alias": outcome.active_alias,
            "error": outcome.error,
        }

    @app.post("/api/ingest/alias/revert")
    def ingest_alias_revert(req: AliasRevertRequest) -> dict[str, Any]:
        """搜索别名整域切回（§10「一键切回旧别名」高危回滚点，M4-cm）：1:1 委派 `revert_alias`，本层不判可否切。

        与上条同一道 B0 门（判门先于触库），同名参数在请求边界先 422（不触库），store 抛错原样上抛（不静默吞掉
        一次索引回退失败）。诚实边界：dev 单 store 别名不影响查询面，本入口兑现的是"人工判定新索引劣化后可整域退回
        保留窗内旧版本"这条运维动作；真实 collection 别名＋操作审计＝放行项②（不伪造企业级隔离，也不伪造审计行）。
        """
        if ctr.ingest is None:  # pragma: no cover
            raise HTTPException(status_code=501, detail="摄取链未装配")
        ctr.ingest.revert_alias(from_alias=req.from_alias, to_alias=req.to_alias)
        return {
            "actor": req.actor,
            "from_alias": req.from_alias,
            "to_alias": req.to_alias,
            "swapped": True,
            "hint": (
                "切回只改搜索别名指向，不改索引内容也不清缓存；执行档单 store 无 collection 物理隔离，"
                "本入口**不核验 72h 保留窗**（指向什么就切什么），真·保留窗与切回审计随双存储落地＝放行项②。"
                "切完经 GET /api/ingest/reconcile 复核账实——该读回只读不传感。"
            ),
        }

    @app.post("/api/ingest/restore")
    def ingest_restore(req: DeleteRestoreRequest) -> dict[str, Any]:
        """误删回滚（§10 tombstone 恢复作业）：1:1 委派 `restore_deletes`，保留期内从 objectstore 重拉正文重放。

        重放同 chunk_id→upsert 幂等复现，非新内容；恢复后管线自跑 B5 对账复核账实（背离即交熔断闸）。
        诚实边界：`recoverable` 只算**窗内且正文仍在**的那一份（M4-cn⑧：窗内但正文已丢的找不回来，不混进可回滚面）；
        过窗=计入 `expired_unrecoverable`、窗内正文真丢=计入 `body_missing`——两者都不可恢复但不是同一回事
        （前者是时间窗已过，后者是账实丢件、要查的是 objectstore）；本层不承诺找回，`actor` 署名口径同上条。
        """
        if ctr.ingest is None:  # pragma: no cover
            raise HTTPException(status_code=501, detail="摄取链未装配")
        outcome = ctr.ingest.restore_deletes(doc_id=req.doc_id, now=time.time())
        return {"actor": req.actor, "doc_id": req.doc_id, **outcome}

    @app.post("/api/ingest/dir")
    def ingest_dir(req: IngestDirRequest) -> dict[str, Any]:
        """目录批量入库（FR-ING-1 存量遍历）：1:1 委派 `gateway/dir_ingest.run_dir_ingest`，本层零判定。

        四道门（未配根目录 501 / 越界 403 / 非目录 422 / 覆盖产物目录 422）在围栏单点执法，HTTP 与 CLI 共用同一口径；
        入库本身走 `IngestPipeline.pull`（B0 写侧总闸、B1 批量删除占比闸、准入/DLP、幂等重放全在管线内）。
        诚实边界：指纹位点＝进程内账（`Container.dir_ingest_state`），**重启后同一目录会当作全新批次**——
        跨进程位点=放行项②；回执 `changed` 里的文件本批**没有**入库（执行档无"内容变了"的安全表达），
        须先下架再重跑，端点不承诺"再点一次就同步成最新"。B1 硬断档下整批以 503 结束且零部分效果（同 1a）。
        """
        if ctr.ingest is None:  # pragma: no cover — 组合根恒装配摄取链
            raise HTTPException(status_code=501, detail="摄取链未装配")
        try:
            return run_dir_ingest(
                ctr,
                directory=req.directory,
                domain=req.domain,
                doc_type=req.doc_type,
                acl_tags=req.acl_tags,
            ) | {"actor": req.actor}
        except DirIngestNotConfigured as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except DirOutsideRoot as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except NotAnIngestDirectory as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except IngestRootOverlapsArtifact as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/source")
    def source(
        chunk_id: str,
        acl: str = "",
        role: str | None = Query(default=None, min_length=1, max_length=64),
    ) -> dict[str, Any]:
        # chunk_id 形如 `{doc}/{ver}#{seq}`，含 `/` 与 `#`——走 query 参数、前端 encodeURIComponent，
        # 不用路径参数（`#` 会被当 URL 锚点截断、`/` 会被路由吃掉）。
        # 可见域口径与 /api/ask 完全一致（role 在位＝后端按画像划分），前端因此无需持有域列表也能读回出处。
        user_acl = _domains(role, [t for t in acl.split(",") if t])
        store = ctr.vectorstore
        getter = getattr(store, "get_chunk", None)
        if getter is None:  # 非内存档：读回正文能力不在，fail-loud 而非静默给错值
            raise HTTPException(status_code=501, detail="出处正文读回仅内存向量档支持（演示档）")
        chunk = getter(chunk_id, user_acl)
        if chunk is None:  # 不存在或越权（pre-filter）——不区分二者，免得探测存在性
            raise HTTPException(status_code=404, detail="出处不可读或无权限")
        return SourceView(
            chunk_id=chunk.chunk_id,
            doc_id=chunk.doc_id,
            doc_version=chunk.doc_version,
            text=chunk.text,
            authority=chunk.authority,  # type: ignore[arg-type]
            breadcrumb=chunk.breadcrumb,
            acl_tags=chunk.acl_tags,
            chunk_type=chunk.chunk_type,
            parent_chunk_id=chunk.parent_chunk_id,
        ).model_dump()

    @app.post("/api/wiki/compile")
    def wiki_compile() -> dict[str, Any]:
        """跑一轮 B 区编译：消费 canonical fan-out → triage 入选→草稿（人审的前一步，1:1 委派 `WikiLoop.run_once`）。

        诚实边界：入选门在 `wiki/service/triage`（制度/规范/runbook/圈定/长文兜底才候选、纪要类不沉淀），
        本层不判也不改判；"入库了但没有草稿"通常是未入选，不是失败。已发布页遇源变更则本轮标脏待重审（FR-WIKI-2）。

        B0 拉闸期本入口返 503 且**一条事件都不消费**（ADR-011：判门在 drain 之前）＝冻结只是推迟，
        复位后再点一次即补上这批编译；不会出现"闸拦住了写侧、fan-out 却被这一轮吃空"的静默丢失。
        """
        return {"new_drafts": ctr.wiki.run_once()}

    @app.get("/api/wiki/pages")
    def wiki_pages(
        role: str | None = Query(default=None, min_length=1, max_length=64),
        acl: str = "",
        status: str | None = Query(default=None, max_length=16),
    ) -> list[dict[str, Any]]:
        """B 区页清单（人审台）：可见域口径与 `/api/ask` 同一 `can_read_field` 闸——不可见的页根本不出现。

        这是 pre-filter 而非 post-filter：正文随页返回（审阅要看依据），故越权域的内容在出网前就被挡掉。
        """
        domains = _domains(role, [t for t in acl.split(",") if t])
        out: list[dict[str, Any]] = []
        for page in ctr.wiki.all_pages():
            if status is not None and page.status != status:
                continue
            if not can_read_field(domains, page.acl_tags):
                continue
            out.append(_page_view(page).model_dump())
        return out

    @app.post("/api/wiki/review")
    def wiki_review(req: WikiReviewRequest) -> dict[str, Any]:
        """人审动作入口（FR-WIKI-1 的"人可点的那一步"）：提交人审 / 署名通过并发布 / 打回。

        门不在本层：迁移合法性与署名要求由 `wiki/service/review.py` 判定，`ReviewError`→409（草稿不得直接发布）；
        发布即 B→A 回写（经 `IngestionPort.admit` 的 F2 ACL 交集硬校验 + B0 写侧总闸，本层零旁路）。
        """
        domains = _domains(req.role, req.acl)
        page = ctr.wiki.page(req.page_id)
        if page is None or not can_read_field(domains, page.acl_tags):
            # 与 /api/source 同一口径：不区分"不存在/越权"，免得探测他域页的存在性
            raise HTTPException(status_code=404, detail="页不可读或无权限")
        try:
            if req.action == "submit":
                ctr.wiki.submit_for_review(page)
                indexed = 0
            elif req.action == "reject":
                ctr.wiki.reject(page)
                indexed = 0
            else:
                event = ctr.wiki.source_event(page.page_id)
                if event is None:
                    raise HTTPException(
                        status_code=409,
                        detail="该页缺编译时的源事件信封（dev 档为进程内态，重启即失），请重新编译生成草稿后再发布",
                    )
                indexed = ctr.wiki.publish(page, event, reviewer=req.reviewer)
        except ReviewError as exc:
            raise HTTPException(status_code=409, detail=f"人审门拒绝：{exc}") from exc
        return {"page": _page_view(page).model_dump(), "indexed": indexed}

    @app.get("/api/admin/funnel")
    def funnel() -> dict[str, Any]:
        return ctr.admin.funnel_stats()

    @app.get("/api/admin/pool")
    def pool() -> dict[str, Any]:
        return ctr.admin.pool_budget()

    @app.get("/api/admin/audit")
    def audit(kind: str = "", limit: int = 50) -> list[dict[str, Any]]:
        kinds = {k for k in kind.split(",") if k} or None
        events = ctr.admin.replay_audit(kinds=kinds)
        return [e.model_dump() for e in events[-max(1, min(limit, 500)) :]]

    # 前端构建产物存在则同源挂载（dist/），否则根路径给一句指路。
    dist = Path(__file__).with_name("web") / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")

    return app


# —— 演示 seed：灌合成跨域语料 + 发布一页 wiki，使答案同时含 source/wiki 权威引用（仅走通 UI 缝，非质量档）——

_DOC1 = (
    "# 差旅报销政策\n\n"
    "第一条：出差住宿标准，一线城市每晚不超过五百元，二线城市每晚不超过三百五十元。\n\n"
    "第二条：出差交通标准，高铁二等座为默认舱位，经济舱机票需提前审批。\n\n"
    "第三条：住宿发票须在返程后七个工作日内提交，逾期不予报销。\n\n"
    "第四条： meals 餐补按每日一百二十元包干，无需发票。\n\n"
)
_DOC2 = (
    "# 员工休假与考勤制度\n\n"
    "第一条：年假按工龄计算，满一年五天，满十年十天。\n\n"
    "第二条：病假需提供医院证明，三天以内直属主管审批，超过三天报人力资源部。\n\n"
    "第三条：加班需事前在系统报备，事后补报不予认定。\n\n"
)
_DOC3 = (
    "# 生产发布运维手册\n\n"
    "第一条：发布前必须通过全部门禁护栏与冒烟测试，禁止 --no-verify 绕过钩子。\n\n"
    "第二条：灰度按 1% 到 10% 到 100% 三段放量，每段观察 P95 延迟与错误率。\n\n"
    "第三条：回滚点为切回上一稳定别名，保留窗口七十二小时。\n\n"
)


_ROLE_PORTRAITS: tuple[tuple[str, list[str]], ...] = (
    ("finance-analyst", ["fin"]),
    ("hr-partner", ["hr"]),
    ("release-oncall", ["eng"]),
    # 跨域角色：可见域＝该角色多条聚合画像的并集（后端算出，不是前端手工拼三个勾）
    ("kb-auditor", ["eng", "fin"]),
)


def seed_demo(container: Container) -> str:
    """演示语料 seed＝**一次写侧动作**：B0 冻结期如实跳过并在回执里说明，绝不让 seed 把读侧一起带走（M4-cs）。

    启动回放（ADR-012）后"重启即解冻"不再成立，而未复位的拉闸若碰上这里，整个门面会在启动时抛错＝
    连"保读侧"这条承诺都兑现不了。seed 不是执法面，跳过它不削弱任何闸（闸仍在每一次真写入口上判）；
    其他异常照旧上抛（fail-loud 不吞）。返回口径：`"seeded"`／`"skipped:<原因>"`，由 `/api/health` 回显。
    """
    if container.ingest is None:  # pragma: no cover — 组合根恒装配摄取链，防御
        return "skipped:摄取链未装配"
    try:
        _seed_demo_writes(container)
    except BreakerTripped as exc:
        return f"skipped:{exc}"
    return "seeded"


def _seed_demo_writes(container: Container) -> None:
    # 0) 角色聚合画像 seed（后端划分知识域的依据）：合成聚合单元，非真实业务统计样本。
    for role, domains in _ROLE_PORTRAITS:
        container.portraits.upsert(
            PortraitAgg(scope="role", key=aggregation_key("role", role), acl_tags=list(domains), sample_size=1)
        )
    # 1) 先摄取 fin 政策文档并即时编译 + 人审发布其 wiki 页——使 fin 域提问能演示"wiki 权威页优先"联动。
    fin_event = container.ingest.process_document(
        Document(doc_id="travel-policy", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_DOC1)
    )
    container.wiki.run_once()
    for page in list(container.wiki.drafts):  # fin 政策文档的草稿页，署名后人审发布为 wiki 权威块
        container.wiki.submit_for_review(page)
        with contextlib.suppress(Exception):  # 演示 seed 尽力而为，发布门语义已由契约测试覆盖
            container.wiki.publish(page, fin_event, reviewer="demo-owner")
    # 2) 再灌 hr/eng 跨域源文档（只入 source，不发布）——供 ACL pre-filter 越权演示。
    for d in (
        Document(doc_id="hr-leave", version=1, domain="hr", doc_type="policy", acl_tags=["hr"], raw_text=_DOC2),
        Document(doc_id="runbook-release", version=1, domain="eng", doc_type="runbook", acl_tags=["eng"], raw_text=_DOC3),
    ):
        container.ingest.process_document(d)


def main() -> int:
    import uvicorn

    uvicorn.run(_create_app(None), host="127.0.0.1", port=8000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
