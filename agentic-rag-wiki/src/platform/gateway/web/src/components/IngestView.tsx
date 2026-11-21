import { useRef, useState } from "react";
import { ingest, ingestFile } from "../api";
import DirIngestPanel from "./DirIngestPanel";
import { DOMAINS } from "../domains";
import type { DocType, IngestFileResult, IngestResult } from "../types";

// 知识中心 · 入库视图（dev/演示档）：两种方式把内容喂进摄取链→切块→准入→索引。
// ①粘贴原文（纯文本）；②上传文件（.docx/.pptx/.pdf/.txt/.md，另加旧版 .doc/.ppt 经本机 Office 转换）——后端 FileParserPort 抽文本后走同一条摄取链。
// 页面底部另有「整目录入库」（M4-ce 1b）：扫服务端允许根目录，逐份走同一条链，回执列五本账。
// 提交只声明"这篇文档对哪些域可见"（acl_tags），真实按主体归因的 ACL 属鉴权放行档，此处不冒充认证。
// 诚实边界：扫描版 PDF（无文本层）由服务端本机 OCR 逐页识别（M4-cf，放行项⑦；供给缺位即 422 指路安装，不静默当文本层）；
// 旧版 .doc/.ppt 靠服务端本机 Word/PowerPoint 转换（不可用或不是真 OLE 容器即明确报错，外部转换器＝放行项㉒）；
// SharePoint/OA 等真实源连接器=放行项㉒；电子表格 .xls/.xlsx 尚无表格正文抽取档（对齐表格原子化口径=放行项⑧）。

const DOC_TYPES: { value: DocType; label: string }[] = [
  { value: "other", label: "其他" },
  { value: "policy", label: "制度 / 政策" },
  { value: "contract", label: "合同" },
  { value: "meeting", label: "会议纪要" },
  { value: "runbook", label: "运维手册" },
  { value: "wiki", label: "wiki" },
];

const ACCEPT = ".docx,.pptx,.pdf,.txt,.md,.doc,.ppt";
const EXT_ICONS: Record<string, string> = {
  docx: "Word",
  doc: "Word 旧版",
  pptx: "PPT",
  ppt: "PPT 旧版",
  pdf: "PDF",
  txt: "TXT",
  md: "MD",
};

// 文件名→合法 doc_id（chunk_id 以 `{doc}/{ver}#{seq}` 编码，doc_id 必须只含字母数字 . _ -）。
function stemToDocId(name: string): string {
  const stem = name.replace(/\.[^.]+$/, "");
  return stem.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[.-]+|[.-]+$/g, "").slice(0, 128);
}

interface Props {
  onDone: () => void;
}

export default function IngestView({ onDone }: Props) {
  const [docId, setDocId] = useState("");
  const [title, setTitle] = useState("");
  const [domain, setDomain] = useState("fin");
  const [docType, setDocType] = useState<DocType>("policy");
  const [aclTags, setAclTags] = useState<string[]>(["fin"]);
  const [rawText, setRawText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IngestFileResult | IngestResult | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const canSubmit =
    docId.trim().length > 0 && aclTags.length > 0 && !busy && (!!file || rawText.trim().length > 0);

  function toggleTag(tag: string) {
    setAclTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  }

  function onPick(f: File | null) {
    setFile(f);
    setResult(null);
    setError(null);
    if (f) {
      if (!docId.trim()) setDocId(stemToDocId(f.name));
      if (!title.trim()) setTitle(f.name.replace(/\.[^.]+$/, ""));
    }
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const meta = {
        doc_id: docId.trim(),
        title: title.trim(),
        domain,
        doc_type: docType,
        acl_tags: aclTags,
      };
      const res = file
        ? await ingestFile(file, meta)
        : await ingest({ ...meta, raw_text: rawText });
      setResult(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-sm border border-rule bg-paper-raised px-3 py-2 text-sm text-ink outline-none focus:border-slate";
  const label = "text-[11px] font-medium text-ink-soft";
  // 页态账（FR-ING-8）：digital 无需解释；scanned/mixed 要如实说清哪几页是认出来的、哪几页认不出而被排除。
  const fileResult = result && "source_file" in result ? result : null;
  const pageNote = !fileResult || fileResult.page_state === "digital"
    ? ""
    : [
        `页态 ${fileResult.page_state === "scanned" ? "扫描版" : "图文混合"}` +
          (fileResult.ocr_pages.length > 0
            ? ` · 本机 OCR 认出第 ${fileResult.ocr_pages.map((p) => p + 1).join("、")} 页`
            : ""),
        fileResult.failed_pages.length > 0
          ? `第 ${fileResult.failed_pages.map((p) => p + 1).join("、")} 页认不出字，未进索引（重扫或另存为带文本层的 PDF 再入库）`
          : "",
      ].filter(Boolean).join("；");

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col overflow-y-auto px-8 py-8">
      <header className="mb-6">
        <h1 className="font-serif text-2xl font-semibold tracking-tight text-ink">知识中心 · 入库</h1>
        <p className="mt-1 text-xs leading-relaxed text-ink-soft">
          粘贴原文或上传文件（{ACCEPT.split(",").join("、")}），投进摄取链（解析 → 切块 → 准入 → 索引），
          成功后即可在问答里按可见域被检索到。这是 dev/演示档：可见域由你声明、不代表已鉴权归属；
          扫描版 PDF 由服务端本机 OCR 逐页识别、旧版 .doc/.ppt 由服务端本机 Office 转换（供给不在位或文件不是真容器时会明确报错而非假装解析），
          电子表格 .xls/.xlsx 仍需表格抽取档——认不出字的页会列进回执且不进索引，不静默跳过。
        </p>
      </header>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={label}>文档 ID（必填，字母数字 . _ -；上传文件时自动取自文件名）</label>
          <input className={field} value={docId} onChange={(e) => setDocId(e.target.value)} placeholder="expense-2026" />
        </div>
        <div>
          <label className={label}>标题</label>
          <input className={field} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="2026 报销补充规定" />
        </div>
        <div>
          <label className={label}>知识域</label>
          <select className={field} value={domain} onChange={(e) => setDomain(e.target.value)}>
            {DOMAINS.map((d) => (
              <option key={d.tag} value={d.tag}>
                {d.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={label}>文档类型</label>
          <select className={field} value={docType} onChange={(e) => setDocType(e.target.value as DocType)}>
            {DOC_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-4">
        <label className={label}>可见域（勾选＝该文档对这些权限域开放）</label>
        <div className="mt-2 flex gap-2">
          {DOMAINS.map((d) => {
            const on = aclTags.includes(d.tag);
            return (
              <button
                key={d.tag}
                type="button"
                aria-pressed={on}
                onClick={() => toggleTag(d.tag)}
                className={
                  "rounded-sm border px-3 py-1.5 text-sm transition-colors " +
                  (on ? "border-seal/60 bg-seal-soft text-ink" : "border-rule text-ink-soft hover:border-slate")
                }
              >
                {d.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* 内容来源：二选一——上传文件 或 粘贴原文 */}
      <div className="mt-5">
        <label className={label}>内容来源</label>
        <div className="mt-2 flex items-center gap-3">
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            onChange={(e) => onPick(e.target.files?.[0] ?? null)}
            className="block w-full cursor-pointer rounded-sm border border-dashed border-rule bg-paper-raised px-3 py-2 text-sm text-ink-soft file:mr-3 file:rounded-sm file:border-0 file:bg-ink file:px-3 file:py-1 file:text-xs file:text-paper"
          />
          {file && (
            <button
              type="button"
              onClick={() => onPick(null)}
              className="shrink-0 rounded-sm border border-rule px-2.5 py-1 text-xs text-ink-soft hover:border-brick hover:text-brick"
            >
              清除
            </button>
          )}
        </div>
        {file && (
          <p className="mt-1.5 font-mono text-[12px] text-slate">
            已选：{file.name} · {EXT_ICONS[file.name.split(".").pop()?.toLowerCase() ?? ""] ?? "未知"} ·{" "}
            {(file.size / 1024).toFixed(1)} KB（上传后由后端抽取正文，忽略下方粘贴框）
          </p>
        )}
      </div>

      <div className={"mt-4 flex min-h-0 flex-1 flex-col" + (file ? " pointer-events-none opacity-40" : "")} aria-hidden={!!file}>
        <label className={label}>正文（未上传文件时必填）</label>
        <textarea
          className={field + " flex-1 resize-none font-mono text-[13px] leading-relaxed"}
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          placeholder={"# 标题\n\n第一条：……\n\n第二条：……"}
        />
      </div>

      <div className="mt-5 flex items-center gap-4">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={submit}
          className="rounded-sm bg-ink px-5 py-2 text-sm font-medium text-paper transition-opacity disabled:opacity-40"
        >
          {busy ? "入库中…" : file ? "解析并入库" : "投入摄取链"}
        </button>
        {!canSubmit && !busy && (
          <span className="text-[11px] text-ink-soft">
            {aclTags.length === 0 ? "请至少勾选一个可见域；" : ""}
            {!file && rawText.trim().length === 0 && docId.trim().length > 0 ? "请填写正文或上传文件；" : ""}
          </span>
        )}
        {error && <span className="text-xs text-brick">失败：{error}</span>}
      </div>

      {result && (
        <div className="mt-5 rounded-sm border border-seal/40 bg-seal-soft/40 px-4 py-3 text-sm text-ink">
          <div className="font-medium">
            已入库 · {result.doc_id} v{result.version}
            {"source_file" in result ? `（来自 ${result.source_file}，抽出 ${result.extracted_chars} 字符）` : ""}
          </div>
          <div className="mt-1 font-mono text-[12px] text-ink-soft">
            checksum {result.checksum.slice(0, 16)} · {result.token_count} tokens · 域 {result.domain} · 可见 {result.acl_tags.join("/") || "（空）"}
          </div>
          <div className="mt-1 text-[12px] text-ink-soft">
            这篇现在只在原文层（source）。要成为答案里优先引用的权威页（wiki），去左侧「权威页审核」跑一轮编译 → 提交人审 →
            审阅人署名发布；也可直接继续入库，或到问答视图按可见域提问验证命中。
          </div>
          {pageNote && (
            <div
              className={
                "mt-1 text-[12px] " +
                (fileResult && fileResult.failed_pages.length > 0 ? "text-brick" : "text-slate")
              }
            >
              {pageNote}
            </div>
          )}
          <button
            type="button"
            onClick={onDone}
            className="mt-3 rounded-sm border border-ink/25 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-ink hover:text-paper"
          >
            去问答验证
          </button>
        </div>
      )}

      {/* 整目录入库：批次元数据沿用上方当前的知识域/类型/可见域选择（真实逐文档元数据=放行项㉒） */}
      <DirIngestPanel domain={domain} docType={docType} aclTags={aclTags} onDone={onDone} />
    </div>
  );
}
