import type { Answer, DocType, Health, IngestFileResult, IngestResult, SourceView, SubjectScope, WikiPageView } from "./types";

// 所有请求打向 HTTP 门面（dev 由 Vite 代理 /api→127.0.0.1:8000，build 后同源 /api）。
// 门面是薄层：只委派 App/Admin façade 与 C 区画像划分，权限 pre-filter 与引用校验都在后端完成，前端不复制这些规则。

const BASE = "/api";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    // 门面的 fail-loud 文案是给读者看的（如"后端不划分任何知识域"），别把整包 JSON 甩到界面上。
    const raw = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}：${errorMessage(raw) || res.statusText}`);
  }
  return (await res.json()) as T;
}

function errorMessage(raw: string): string {
  try {
    const detail = (JSON.parse(raw) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d) => (d as { msg?: string }).msg ?? "").filter(Boolean).join("；");
    }
  } catch {
    return raw;
  }
  return raw;
}

export async function getHealth(): Promise<Health> {
  return json(await fetch(`${BASE}/health`));
}

// 可见域读回（M4-ca）：知识域由后端按角色聚合画像划分，前端只显不可改；画像未建立即 409。
export async function getScope(role: string): Promise<SubjectScope> {
  const q = new URLSearchParams({ role });
  return json(await fetch(`${BASE}/scope?${q.toString()}`));
}

export async function ask(question: string, role: string, traceId?: string): Promise<Answer> {
  return json(
    await fetch(`${BASE}/ask`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question, role, trace_id: traceId ?? null }),
    }),
  );
}

// chunk_id 形如 `{doc}/{ver}#{seq}`，含 `/` 与 `#`——走 query 参数，URLSearchParams 负责 percent 编码。
// 读回出处只带 role：可见域仍由后端解析，前端不持有权限列表（与 /api/ask 同一口径）。
export async function getSource(chunkId: string, role: string): Promise<SourceView> {
  const q = new URLSearchParams({ chunk_id: chunkId, role });
  return json(await fetch(`${BASE}/source?${q.toString()}`));
}

export interface IngestPayload {
  doc_id: string;
  title: string;
  domain: string;
  doc_type: DocType;
  acl_tags: string[];
  raw_text: string;
}

// 投料入库（dev/演示档）：门面 1:1 委派 IngestPipeline，B0/B4/DLP 准入都在后端执法，前端不复制这些规则。
export async function ingest(payload: IngestPayload): Promise<IngestResult> {
  return json(
    await fetch(`${BASE}/ingest`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ version: 1, ...payload }),
    }),
  );
}

export interface IngestFileMeta {
  doc_id: string;
  title: string;
  domain: string;
  doc_type: DocType;
  acl_tags: string[];
}

// 整份文件字节 base64 上送（非 multipart，免引服务端新依赖）；后端 FileParserPort 抽文本→同一摄取链。
async function bytesToBase64(buf: ArrayBuffer): Promise<string> {
  const bytes = new Uint8Array(buf);
  let bin = "";
  const CH = 0x8000; // 分块避免 String.fromCharCode 参数过多（大文件栈溢出）
  for (let i = 0; i < bytes.length; i += CH) {
    bin += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + CH)));
  }
  return btoa(bin);
}

export async function ingestFile(file: File, meta: IngestFileMeta): Promise<IngestFileResult> {
  const contentB64 = await bytesToBase64(await file.arrayBuffer());
  return json(
    await fetch(`${BASE}/ingest/file`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ version: 1, filename: file.name, content_b64: contentB64, ...meta }),
    }),
  );
}

// —— B 区权威页人审台（M4-cb）：编译 / 页清单 / 人审动作，全部只委派后端，前端不判迁移合法性 ——
// 跑一轮编译：消费 canonical 变更→triage 入选→草稿。未入选即无草稿，不是失败（门在 wiki/service/triage.py）。
export async function compileWiki(): Promise<{ new_drafts: number }> {
  return json(await fetch(`${BASE}/wiki/compile`, { method: "POST" }));
}

// 页清单按当前角色的可见域由后端过滤（pre-filter，非前端藏）；越权页在这里根本不会出现。
export async function listWikiPages(role: string): Promise<WikiPageView[]> {
  const q = new URLSearchParams({ role });
  return json(await fetch(`${BASE}/wiki/pages?${q.toString()}`));
}

export type ReviewAction = "submit" | "approve" | "reject";

// 人审动作：approve 必须署名（门在状态机，空署名后端即 409）；发布即写索引，B0 拉闸期后端 503。
export async function reviewWiki(pageId: string, action: ReviewAction, role: string, reviewer = ""): Promise<{ page: WikiPageView; indexed: number }> {
  return json(
    await fetch(`${BASE}/wiki/review`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ page_id: pageId, action, role, reviewer }),
    }),
  );
}

// —— A 区生命周期（M4-ce）：下架进墓碑账，保留期内可回滚。判定与落地都在 IngestPipeline，前端不复制规则。——

// 回执形状＝后端 `apply_changes` 的计账字段，一个数都不在前端另算（分册口径见 docs/runbooks/ingest_retire.md）。
export interface ChangeReceiptBase {
  doc_id: string;
  actor: string;
  recoverable: number;
  total: number;
  deleted: number;
  reindexed: number;
  acl_rewritten: number;
  acl_pending: number;
  cache_evicted: number;
  hint: string;
}

// 下架回执：`deleted`＝两路索引同清的块数，`recoverable`＝进入可回滚墓碑账的条数。
export interface RetireReceipt extends ChangeReceiptBase {
  kind: "delete";
}

// 改可见域回执：`acl_rewritten`＝已按新域重写的源块数，`acl_pending`＝正文拉不到、未能重写而留在 B5 债里的块数
// （如实报数，不当作 0）。`effects_pending`＝效应①PG 权限行（真实双存储=放行项②），前端原样列出。
// 注意：这里兑现的是源块侧即刻生效；该源已发布的权威块要到下一轮编译才撤（`hint` 明写此窗），
// 所以文案不得写"受众已改完"——前端不替后端圆场。
export interface RescopeReceipt extends ChangeReceiptBase {
  kind: "acl_change";
  pending_acl_blocks: number;
  effects_done: string[];
  effects_pending: string[];
}

export type ChangeResult = RetireReceipt | RescopeReceipt;

export interface TombstoneRow {
  doc_id: string;
  version: number;
  domain: string;
  chunk_ids: string[];
  deleted_at: number;
  retain_until: number;
  seconds_left: number;
  recoverable: boolean;
  has_body: boolean;
}

export interface TombstoneView {
  now: number;
  items: TombstoneRow[];
  // 三个数同一口径（M4-cn⑧）：窗内数 = recoverable + body_missing；`recoverable` 才是真能找回的那一份。
  recoverable: number;
  body_missing: number;
  expired: number;
}

// 下架＝写侧动作：confirm 恒为 true（界面上的二次确认），B0 拉闸/B1 超限时后端 503，前端只转述不判。
export async function retireDocument(docId: string, actor: string): Promise<RetireReceipt> {
  return json(
    await fetch(`${BASE}/ingest/change`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ doc_id: docId, kind: "delete", actor, confirm: true }),
    }),
  );
}

// 改可见域＝受众变更，同样走变更流（不新端点、不复制判据）。acl_after 必须显式给（含"清空"也要显式声明），
// 后端 schema 层就拒缺省；效应①PG 权限行属放行项②，回执 effects_pending 里如实挂着。
export async function rescopeDocument(docId: string, aclBefore: string[], aclAfter: string[], actor: string): Promise<RescopeReceipt> {
  return json(
    await fetch(`${BASE}/ingest/change`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        doc_id: docId,
        kind: "acl_change",
        actor,
        confirm: true,
        acl_before: aclBefore,
        acl_after: aclAfter,
      }),
    }),
  );
}

export async function listTombstones(): Promise<TombstoneView> {
  return json(await fetch(`${BASE}/ingest/deletes`));
}

// docId=null＝回滚全部待恢复墓碑；恢复＝重放正文回索引，非"改标记"。
// `recoverable` 只算窗内且正文仍在的那一份（M4-cn⑧）；窗内但正文已丢的单列 `body_missing`，与超窗的 `expired_unrecoverable` 分账。
export async function restoreTombstones(
  docId: string | null,
  actor: string,
): Promise<{
  actor: string;
  doc_id: string | null;
  restored: number;
  recoverable: number;
  body_missing: number;
  expired_unrecoverable: number;
}> {
  return json(
    await fetch(`${BASE}/ingest/restore`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ doc_id: docId, actor, confirm: true }),
    }),
  );
}

// —— A 区目录批量入库（M4-ce 1b）：整目录走同一条摄取链。前端只送相对目录与批次元数据，
// 四道门（未配根 501／越界 403／非目录 422／覆盖产物目录 422）与熔断、准入全在后端执法，此处不复制判据。

export interface DirBucketRow {
  file: string;
  doc_id?: string;
  reason?: string;
}

export interface DirIngestReceipt {
  root: string;
  directory: string;
  relative: string;
  delete_scan: "root" | "subdir-off";
  domain: string;
  doc_type: string;
  acl_tags: string[];
  actor: string;
  ingested: number;
  not_ingested: number;
  change_total: number;
  change_deleted: number;
  change_reindexed: number;
  change_cache_evicted: number;
  skipped: DirBucketRow[];
  changed: DirBucketRow[];
  conflicts: DirBucketRow[];
  unchanged: DirBucketRow[];
  reingested: DirBucketRow[];
  hint: string;
}

// directory=""＝允许根目录本身；confirm 恒 true（一批文件＝一次写侧批量动作，须显式确认）。
export async function ingestDir(payload: {
  directory: string;
  domain: string;
  doc_type: DocType;
  acl_tags: string[];
  actor: string;
}): Promise<DirIngestReceipt> {
  return json(
    await fetch(`${BASE}/ingest/dir`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...payload, confirm: true }),
    }),
  );
}
