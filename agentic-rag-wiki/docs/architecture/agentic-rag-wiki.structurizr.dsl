workspace "Agentic RAG + LLM wiki 知识库" "现档 C4 模型（M4-cn 之后，2026-10-14）：三分区 × 平台平面 × 组合根唯一注入点。每个节点都可追溯到 src/ 下的真实文件，逐条对照见 agentic-rag-wiki.evidence.md；低置信节点（真实源系统、企业档双存储）已用标签标出，不得当作已交付现状读。" {
  !identifiers hierarchical

  model {
    // ── L1 人 ──────────────────────────────────────────────────────────
    employee = person "企业员工（读者）" "在有权可见域内提问，拿带出处的答案与 wiki 权威页"
    curator = person "知识管理员" "入库、下架与恢复、改可见域、权威页人审署名发布"
    ops = person "运维 / 值班" "读回漏斗·分池·审计；硬断双人复位（复位唯一入口＝CLI）"

    // ── L1 系统边界 ────────────────────────────────────────────────────
    kb = softwareSystem "Agentic RAG + LLM wiki 知识库" "企业知识库问答 + wiki 权威页编译回路；v1 执行档＝单机可跑通的那一档" {

      // ── L2 容器 ──────────────────────────────────────────────────────
      web = container "Web 前端" "chat 主区 + 右侧 wiki 联动 + 入库/下架/改域/审核台；10 个视图组件" "Vite + React + TS + Tailwind v4 + Motion" {
        tags "BrowserApp"
      }
      gateway = container "Gateway 应用（对外门面 + 组合根）" "执行档单进程；HTTP 21 路由 + CLI 13 注册位 + 装配表；进程拆分属 ADR-005 与实机放行项" "Python 3.12 + FastAPI" {
        tags "LogicalInProcess"

        gwComposition = component "组合根 composition.py" "全仓唯一同时可见 provider 具体实现与三分区的模块：按装配表把端口接成实现，产出 Container（22 字段）" "Python"
        gwHttp = component "HTTP 门面 http.py" "21 条路由 1:1 委派 App/Admin，本层零业务逻辑、不改契约" "FastAPI"
        gwCli = component "CLI 命令面 cli.py" "ask / ingest / admin{audit-replay,pool,funnel,degrade-plan,reset,reconcile,compensate}" "argparse"
        gwApp = component "App 问答与入库 façade" "问答链入口 + 写侧动作前置判门" "Python"
        gwAdmin = component "Admin 运维读回 façade" "审计回放 / 分池三本账 / 漏斗四层 / 降档判级 / 双人复位" "Python"
        gwGuard = component "PolicyGate + InflightTracker" "GuardPort 实现：把熔断判定内核包成写侧闸与在飞记账，经同一本 TripLedger" "Python"
        gwAuditAdapter = component "AuditAdapter" "把审计落成跨区端口，供三分区写同一 append-only 册" "Python"
        gwWebStatic = component "web/dist 同源挂载" "构建产物存在时由门面同源伺服，省一个部署单元" "StaticFiles"
      }
      ingest = container "A 区 摄取与问答分区" "唯一的索引写入者；问答链也在本区 service/ask" "Python" {
        tags "LogicalInProcess"

        ingPipeline = component "IngestPipeline runtime/pipeline.py" "入库/变更/下架/恢复/补偿的唯一动作入口；判门在写侧之前，一次动作只传感一次" "Python"
        ingCanonicalize = component "canonicalize + chunker + intake" "正文→五种 CanonicalNode→D-2 档位切块→表格父链（ADR-010 node 级决议）" "Python"
        ingBudget = component "PrecedentLedger service/budget.py" "D-3 判例预算：cap＝文档 token×10%，三级漏斗唯一决议入口，越限回落默认 profile＋人审位" "Python"
        ingAdmission = component "admission + dlp service" "入库准入：ACL 域、DLP、B4 注入探测（v1 shadow）" "Python"
        ingInventory = component "CanonicalInventory repo" "B5 双册账：源块册 (doc_id,version) ＋回写块册 chunk_id→page_id" "Python"
        ingDeleteLedger = component "DeleteLedger repo" "墓碑账：窗内 ∧ 正文在 才可回滚，三分账 recoverable/body_missing/expired_unrecoverable" "Python"
        ingSourcePort = component "SourcePort repo/source.py" "源连接器契约（list_documents/poll_changes）＋ DevSource 内存档；真实连接器＝放行项㉒" "Python"
        ingReconcile = component "reconcile + compensate + reindex" "B5 对账（只读不传感）／整批补偿（不臆造页）／版本化影子重建" "Python"
        ingAskRuntime = component "AnswerAgent runtime/ask.py" "问答链编排：pre-filter→漏斗→邻居回取→引用校验→出口 DLP→缓存准入" "Python"
        ingAskService = component "service/ask 九个纯函数件" "cache 键／citation／fusion RRF／funnel 四层账／ladder 降档阶梯／planner 多跳／policy 置信拒答／neighbor 回取／cost 核算" "Python"
        ingPort = component "IngestionPort（B 区唯一回写通道）" "admit 权威块 / withdraw 撤块 / is_indexed 依据存活判定" "Protocol"
      }
      wiki = container "B 区 wiki 编译分区" "不得直写 A 区索引，回写只经 IngestionPort" "Python" {
        tags "LogicalInProcess"

        wkLoop = component "WikiLoop runtime/loop.py" "消费变更队列→triage→重合成语义→脏页标记与撤块；publish 前置依据存活门" "Python"
        wkCompile = component "compile service" "增量重编译，产出草稿页与回写块候选" "Python"
        wkTriage = component "triage service" "三类信号判是否值得立页，判不准降级不立页" "Python"
        wkReview = component "review service" "人审状态机：草稿→in_review→署名通过→published；无独立撤发布按钮" "Python"
        wkPages = component "PageRegistry repo/pages.py" "页册：同 page_id 重编译顶替旧页，状态与署名在这本账上" "Python"
      }
      memory = container "C 区 记忆与画像分区" "只读聚合与提案；安全参数不得自动写（跨区三禁③）" "Python" {
        tags "LogicalInProcess"

        memLoop = component "runtime/loop.py" "长期聚合回路（执行档由门面触发，无常驻调度）" "Python"
        memAuditSink = component "AuditSink service/audit_sink.py" "append-only 审计落盘，唯一审计写入者" "Python"
        memDomainScope = component "domain_scope service" "读者角色→可见知识域的单一解析点（问答 pre-filter 用这一处）" "Python"
        memPortrait = component "portrait_policy + DevPortraitStore" "画像合规三层保护（聚合粒度/会话 TTL/ACL 读门）＋单机 JSON 快照" "Python"
        memParam = component "param_proposal + report" "参数提案走人审（双级红线：安全参数不可自动改）" "Python"
      }
      platform = container "平台平面（契约·配置·Provider·熔断·护栏）" "横切能力唯一入口；分区代码零 SDK import" "Python" {
        tags "LogicalInProcess"

        pfTypes = component "platform/types 契约模型" "E3 冻结契约：15 个模型入基线快照（含 ChunkDecision/NodeDecision/CanonicalNode）" "pydantic"
        pfConfig = component "Settings config/settings.py" "配置单一口径；幻影键由 config_surface_check 双向机检" "pydantic-settings"
        pfPorts = component "providers/ports 15 个 Protocol" "Embedding/VectorStore/Lexical/ObjectStore/Mq/Rerank/Llm/Ocr/Guard/Graph/Cache/FileParser/…（G5 可换）" "Protocol"
        pfProviders = component "providers 28 个实现" "dev/local/cloud 三档 + milvus/pgvector/s3/kafka/langgraph；选档缺驱动即 fail-loud，绝不静默回落" "Python"
        pfBreaker = component "breaker engine + rules + pool + slo" "B0–B5 判定内核（零 IO 纯函数，8 个规则 YAML）＋分池三本账＋错误预算判级" "Python"
        pfHarness = component "harness 10 道护栏门" "check_imports/schemas/contract_compat/breaker_rules/param_class/lifecycle/config_surface/backend_choice/runbook/deps + secret_scan" "Python（构建期/提交期）" {
          tags "BuildTime"
        }
      }

      vectorIndex = container "向量索引（可重建派生）" "唯一事实源是 canonical＋清单；索引可滞后不可背离→背离即 B5" "MemoryVectorStore / pgvector / Milvus" {
        tags "Database"
      }
      lexicalIndex = container "词法索引 BM25" "与向量路同生同灭：两路任一路留旧副本即视为背离" "Bm25Index（dev 进程内）" {
        tags "Database"
      }
      cacheStore = container "语义缓存" "键首段恒为逗号连接的 ACL 域指纹；无 TTL，靠四条路径主动清（删撤/回写/改域/重灌）" "MemoryCache" {
        tags "Database"
      }
      objectStore = container "canonical 正文对象库" "正文与决议记录归档；恢复可行性看这里有没有正文" "FileSystemObjectStore / S3" {
        tags "Database"
      }
      queueStore = container "变更队列" "fan-out 变更事件→B 区编译；积压深度是 MqPort 原语" "PgQueueMq / KafkaMq" {
        tags "Queue"
      }
      auditLog = container "审计流（append-only）" "运行事实的唯一账；PG 物理分表＝放行项②" "JSONL 文件" {
        tags "Database"
      }
      portraitDb = container "角色画像册" "长期聚合画像（角色→可见知识域），与审计分文件" "单机 JSON / PG 分表＝放行项②" {
        tags "Database"
      }
      artifacts = container "产物目录 workspace/" "B5 对账报告、墓碑、草稿与重建留痕；观测入口不得自己成故障源" "文件" {
        tags "FileStore"
      }
    }

    // ── L1 外部系统 ────────────────────────────────────────────────────
    cloudLlm = softwareSystem "云 LLM API" "主档；失败经审计上报并自动回退本地，不判 B3" {
      tags "External"
    }
    cloudEmbedRerank = softwareSystem "云 Embedding / Rerank API" "可选档位；选云档即触发凭证与出网白名单双预检" {
      tags "External"
    }
    localModels = softwareSystem "本地模型运行时" "Qwen3-Embedding-0.6B fp16 + bge-reranker-v2-m3（sentence-transformers，CUDA 12.8）" {
      tags "External"
    }
    rapidOcr = softwareSystem "RapidOCR / onnxruntime" "扫描页供给侧识别，纯 CPU、权重随 wheel 分发＝识别零外呼" {
      tags "External"
    }
    msOffice = softwareSystem "本机 Office（COM 转换）" "旧版 OLE doc/ppt→现代容器，容器真伪门在前" {
      tags "External"
    }
    realSources = softwareSystem "真实源系统（SharePoint / OA / 网盘 / Git）" "低置信·未落地：只有 SourcePort 契约与 DevSource 内存档，真实读取端＝放行项㉒" {
      tags "External", "Unverified"
    }
    pgEnterprise = softwareSystem "企业档 PG（元数据·权限·审计）" "低置信·未落地：唯一事实源档位，双存储背离债真口径＝放行项②" {
      tags "External", "Unverified"
    }

    // ── 关系：人 → 面 ──────────────────────────────────────────────────
    employee -> kb "提问、看答案与出处、看权威页"
    curator -> kb "入库、下架与恢复、改可见域、人审署名发布"
    ops -> kb "读回运维账、双人复位硬断"
    employee -> web "浏览器问答" "HTTPS"
    curator -> web "知识中心视图" "HTTPS"

    // ── 关系：门面内部 ─────────────────────────────────────────────────
    web -> kb.gateway "调 21 条路由（REST）" "JSON"
    kb.gateway.gwHttp -> kb.gateway.gwApp "1:1 委派（本层零业务逻辑）"
    kb.gateway.gwHttp -> kb.gateway.gwAdmin "1:1 委派（只读账本）"
    kb.gateway.gwCli -> kb.gateway.gwApp "同一本账同一口径"
    kb.gateway.gwCli -> kb.gateway.gwAdmin "命令面不另算读数"
    ops -> kb.gateway "复位与运维读回只能走 CLI（HTTP 面故意不暴露 reset）" "命令行"
    kb.gateway.gwWebStatic -> web "同源伺服构建产物" "StaticFiles"
    kb.gateway.gwComposition -> kb.gateway.gwHttp "装配产出 App/Admin/WikiLoop/Pipeline 后交给门面"
    kb.gateway.gwComposition -> kb.ingest.ingPipeline "注入端口实现（唯一注入点）"
    kb.gateway.gwComposition -> kb.wiki.wkLoop "注入"
    kb.gateway.gwComposition -> kb.memory.memAuditSink "注入"
    kb.gateway.gwComposition -> kb.platform.pfProviders "按装配表逐端口选档"
    kb.gateway.gwComposition -> kb.platform.pfBreaker "load_rules + PoolBudget.for_hard"
    kb.gateway.gwApp -> kb.ingest.ingAskRuntime "读侧：问答链"
    kb.gateway.gwApp -> kb.ingest.ingPipeline "写侧：入库/变更/下架/恢复/补偿"
    kb.gateway.gwApp -> kb.gateway.gwGuard "判门在写侧之前，一次动作只传感一次"
    kb.gateway.gwAdmin -> kb.platform.pfBreaker "读回分池·漏斗·降档判级"
    kb.gateway.gwGuard -> kb.platform.pfBreaker "调判定内核（纯函数，无 IO）"
    kb.gateway.gwGuard -> kb.memory.memAuditSink "触发即记账"
    kb.gateway.gwAuditAdapter -> kb.memory.memAuditSink "跨区审计端口"

    // ── 关系：A 区摄取链 ───────────────────────────────────────────────
    kb.ingest.ingPipeline -> kb.ingest.ingAdmission "准入判定"
    kb.ingest.ingPipeline -> kb.ingest.ingCanonicalize "正文→节点→切块"
    kb.ingest.ingCanonicalize -> kb.ingest.ingBudget "D-1 决议 + D-3 判例预算"
    kb.ingest.ingPipeline -> kb.ingest.ingInventory "双册记账"
    kb.ingest.ingPipeline -> kb.ingest.ingDeleteLedger "墓碑入账"
    kb.ingest.ingSourcePort -> kb.ingest.ingPipeline "pull→process_document / apply_changes"
    realSources ..> kb.ingest.ingSourcePort "未落地：真实读取端需外部供给＝放行项㉒" "无实现" "Unverified"
    kb.ingest.ingPipeline -> kb.platform.pfPorts "经端口取能力（不 import SDK）"
    kb.platform.pfPorts -> kb.platform.pfProviders "同接口多实现（G5 可换）"
    kb.platform.pfProviders -> kb.vectorIndex "写/删/查向量"
    kb.platform.pfProviders -> kb.lexicalIndex "写/删/查词法"
    kb.platform.pfProviders -> kb.objectStore "正文与决议留档"
    kb.platform.pfProviders -> kb.queueStore "发布变更事件"
    kb.platform.pfProviders -> localModels "本地 embedding/rerank/LLM 兜底"
    kb.platform.pfProviders -> cloudLlm "云 LLM 主档（出网白名单）" "HTTPS"
    kb.platform.pfProviders -> cloudEmbedRerank "可选云档（出网白名单）" "HTTPS"
    kb.platform.pfProviders -> rapidOcr "扫描页供给侧识别"
    kb.platform.pfProviders -> msOffice "旧版 OLE 转换"
    kb.platform.pfProviders ..> pgEnterprise "企业档双存储未接线＝放行项②" "无实现" "Unverified"

    // ── 关系：A 区问答链 ───────────────────────────────────────────────
    kb.ingest.ingAskRuntime -> kb.ingest.ingAskService "漏斗归属·RRF·阶梯·置信·邻居·成本"
    kb.ingest.ingAskService -> kb.cacheStore "①层命中直返（键含 ACL 域指纹）"
    kb.ingest.ingAskService -> kb.vectorIndex "向量召回 topK"
    kb.ingest.ingAskService -> kb.lexicalIndex "BM25 召回 topK"
    kb.ingest.ingAskService -> kb.memory.memDomainScope "pre-filter：角色→可见域（恒在最前，禁 post-filter）"
    kb.ingest.ingAskService -> kb.gateway.gwGuard "在飞记账 + 降档取档"
    kb.ingest.ingAskRuntime -> kb.wiki.wkPages "②层优先引权威页（跨区只读，不回写）"

    // ── 关系：B 区编译与回写（跨区三禁②）──────────────────────────────
    kb.gateway.gwHttp -> kb.wiki.wkLoop "1:1 委派：run_once / all_pages / page / publish / reject"
    kb.wiki.wkLoop -> kb.wiki.wkReview "人审状态机（提交/署名通过/打回）"
    kb.wiki.wkLoop -> kb.queueStore "消费变更事件"
    kb.wiki.wkLoop -> kb.wiki.wkTriage "判是否立页"
    kb.wiki.wkLoop -> kb.wiki.wkCompile "重合成/标脏"
    kb.wiki.wkCompile -> kb.wiki.wkPages "草稿入册"
    curator -> web "审核台：跑一轮编译→提交人审→署名通过并发布" "HTTPS"
    kb.wiki.wkReview -> kb.wiki.wkPages "状态机流转（草稿→in_review→published）"
    kb.wiki.wkLoop -> kb.ingest.ingPort "回写只经 IngestionPort：admit / withdraw"
    kb.ingest.ingPort -> kb.ingest.ingPipeline "落两路索引 + 回写块册记账"
    kb.ingest.ingPort -> kb.memory.memAuditSink "发布/撤块留痕"
    kb.wiki.wkLoop -> kb.ingest.ingInventory "依据存活判定（读 A 区唯一那本清单，不自立第二本账）"

    // ── 关系：C 区回路 ─────────────────────────────────────────────────
    kb.memory.memLoop -> kb.memory.memPortrait "角色→可见域聚合（读侧降权）"
    kb.memory.memParam ..> kb.platform.pfBreaker "提案须经人审；安全参数不可自动改＝跨区三禁③（执行档无自动写通道）" "无实现" "Unverified"
    kb.platform.pfBreaker -> kb.platform.pfTypes "读契约模型与规则 YAML"
    kb.platform.pfHarness ..> kb.gateway.gwComposition "构建期：扫 import 方向与装配表后端分支" "静态扫源码" "BuildTime"
    kb.platform.pfHarness ..> kb.ingest.ingPipeline "构建期：生命周期/对账/切块决议契约机检" "静态扫源码" "BuildTime"
  }

  // ── 视图 ────────────────────────────────────────────────────────────
  views {
    systemContext kb "c1-context" "L1：谁在用这个系统，系统又碰了哪些外部能力。" {
      include *
      autolayout lr
    }

    container kb "c2-containers" "L2：三分区 × 平台平面 × 门面 × 数据存储；标 Unverified 的属实机放行项，不是现状。" {
      include *
      autolayout lr
    }

    component kb.gateway "c3-gateway" "L3：对外三面与唯一注入点——所有跨区触达都在这层接上。" {
      include *
      autolayout lr
    }

    component kb.ingest "c3-ingest" "L3：A 区——唯一的索引写入者，摄取链与问答链同区。" {
      include *
      autolayout lr
    }

    component kb.wiki "c3-wiki" "L3：B 区——编译回路与人审门，回写只经 IngestionPort。" {
      include *
      autolayout lr
    }

    component kb.memory "c3-memory" "L3：C 区——审计唯一写入者与画像/提案回路。" {
      include *
      autolayout lr
    }

    component kb.platform "c3-platform" "L3：平台平面——契约、配置、端口/实现、熔断内核、护栏门。" {
      include *
      autolayout lr
    }

    styles {
      element "Person" {
        shape person
        background #1f3a5f
        color #ffffff
      }
      element "BrowserApp" {
        background #2f6f6b
        color #ffffff
      }
      element "LogicalInProcess" {
        background #35507a
        color #ffffff
      }
      element "Database" {
        shape cylinder
        background #5a5f7a
        color #ffffff
      }
      element "Queue" {
        background #6a5a7a
        color #ffffff
      }
      element "FileStore" {
        background #6b6250
        color #ffffff
      }
      element "BuildTime" {
        background #7a6a3a
        color #ffffff
      }
      element "External" {
        background #9aa4b8
        color #101423
      }
      element "Unverified" {
        background #ffffff
        color #7a3a3a
        stroke #b04a4a
      }
      element "低置信·禁令" {
        color #b04a4a
        thickness 2
      }
    }
  }
}
