// 与后端 JSON 契约 1:1：Answer/Citation 见 types/answer.py，SourceView/SubjectScope 见 gateway/http.py。
// 前端不在此层做任何检索/权限判断——可见域由后端按 C 区聚合画像划分（M4-ca），pre-filter 在后端 App 内执法。

export type Authority = "source" | "wiki";

export interface Citation {
  chunk_id: string;
  doc_id: string;
  doc_version: number;
  breadcrumb: string[];
  page_no: number | null;
  authority: Authority;
}

export interface Answer {
  question: string;
  text: string;
  citations: Citation[];
  confidence: number;
  refused: boolean;
  hops: number;
  degraded: boolean;
  trace_id: string;
}

export type ChunkType = "text" | "table" | "code";

export interface SourceView {
  chunk_id: string;
  doc_id: string;
  doc_version: number;
  text: string;
  authority: Authority;
  breadcrumb: string[];
  acl_tags: string[];
  // D-2 块型（FR-ING-7/ADR-010）：出处卡片据此分排——表格/代码不按散文段落渲染。
  chunk_type: ChunkType;
  // 表格退化行组的父块 id；非空＝本块是"同表行组"的一员，首组为父（null）。
  parent_chunk_id: string | null;
}

export interface Health {
  status: string;
  assembly: string;
  quality: string;
}

// 主体可见域（M4-ca）：知识域由后端按 C 区角色聚合画像划分，前端只显不可改。
export interface SubjectScope {
  role: string;
  domains: string[];
}

// 入库回执 = 后端 CanonicalEvent（types/document.py）：只带指针+元数据，正文走 ObjectStore（F1）。
export interface IngestResult {
  doc_id: string;
  version: number;
  domain: string;
  acl_tags: string[];
  checksum: string;
  store_key: string;
  token_count: number;
  origin: string;
  doc_type: string;
  op: string;
}

export type DocType = "policy" | "contract" | "meeting" | "wiki" | "runbook" | "other";

// 文件入库回执：在 IngestResult 之上多门面回填的解析元数据。
// page_state/ocr_pages/failed_pages 由解析边界逐页判出（FR-ING-8，ADR-009）：抽出字符数只是半个真相，
// "哪几页是认出来的、哪几页认不出而被排除"必须在回执里看得见。
export type PageState = "digital" | "scanned" | "mixed";

export interface IngestFileResult extends IngestResult {
  source_file: string;
  extracted_chars: number;
  page_state: PageState;
  ocr_pages: number[];
  failed_pages: number[];
}

// B 区 wiki 页（M4-cb 人审台）：状态字段见后端 wiki/service/review.py 状态机，正文随页返回供审阅。
export type WikiPageStatus = "draft" | "in_review" | "published" | "stale" | "retracted";

export interface WikiSectionView {
  heading: string;
  body: string;
  source_chunk_ids: string[];
}

export interface WikiPageView {
  page_id: string;
  domain: string;
  title: string;
  status: WikiPageStatus;
  acl_tags: string[];
  source_doc_ids: string[];
  reviewer: string;
  triage_reason: string;
  version: number;
  sections: WikiSectionView[];
}
