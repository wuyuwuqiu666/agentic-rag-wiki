import { useState } from "react";
import { ingestDir } from "../api";
import type { DirBucketRow, DirIngestReceipt } from "../api";
import type { DocType } from "../types";

// 目录批量入库面板（M4-ce 1b，FR-ING-1 存量遍历）：把服务端允许根目录里的一批文件喂进同一条摄取链。
// 前端只送**相对**目录 + 批次元数据；路径围栏、B0 总闸、B1 删除占比、准入/DLP 全在后端单点执法（gateway/dir_ingest.py）。
// 可见域沿用上方入库表单的勾选：一批文件在同一批里共享元数据缺省，逐文档真实元数据＝放行项㉒。
// 诚实边界：指纹位点＝服务端进程内账，重启后同目录会被当作全新批次；回执 changed 的文件本批没有入库。

const BUCKETS: { key: keyof DirIngestReceipt; label: string; note: string }[] = [
  { key: "skipped", label: "未解析", note: "解析失败/超限/空正文，原因逐条列出" },
  { key: "conflicts", label: "撞 ID", note: "文件名归一后与另一文件同 ID，后到者未覆盖" },
  { key: "changed", label: "内容已变", note: "本批未入库：到「下架与恢复」下架该文档，再重跑本目录" },
  { key: "unchanged", label: "无变化", note: "指纹相同，幂等跳过" },
  { key: "reingested", label: "重新入库", note: "曾被下架而目录里仍在，按源为真相重入" },
];

function Rows({ rows }: { rows: DirBucketRow[] }) {
  return (
    <ul className="mt-1 space-y-0.5">
      {rows.map((r) => (
        <li key={r.file} className="font-mono text-[11px] leading-relaxed text-ink-soft">
          {r.file}
          {r.doc_id ? ` → ${r.doc_id}` : ""}
          {r.reason ? ` · ${r.reason}` : ""}
        </li>
      ))}
    </ul>
  );
}

interface Props {
  domain: string;
  docType: DocType;
  aclTags: string[];
  onDone: () => void;
}

export default function DirIngestPanel({ domain, docType, aclTags, onDone }: Props) {
  const [directory, setDirectory] = useState("");
  const [actor, setActor] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<DirIngestReceipt | null>(null);

  const canSubmit = actor.trim().length > 0 && aclTags.length > 0 && !busy;

  async function submit() {
    setBusy(true);
    setError(null);
    setReceipt(null);
    try {
      setReceipt(await ingestDir({ directory: directory.trim(), domain, doc_type: docType, acl_tags: aclTags, actor: actor.trim() }));
    } catch (e) {
      setError((e as Error).message); // 门面的拒绝理由原文照实显示（501 未配根／403 越界／422 非目录·覆盖产物／503 熔断）
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-sm border border-rule bg-paper-raised px-3 py-2 text-sm text-ink outline-none focus:border-slate";
  const label = "text-[11px] font-medium text-ink-soft";

  return (
    <section className="mt-8 border-t border-rule pt-5">
      <h2 className="font-serif text-base font-semibold text-ink">整目录入库</h2>
      <p className="mt-1 text-[12px] leading-relaxed text-ink-soft">
        扫描服务端允许根目录（`APP_INGEST_SOURCE_DIR`）内的一个相对目录，把可读文件逐个送进同一条摄取链。
        知识域／类型／可见域沿用上方表单当前的选择；绝对路径与越出根目录的相对路径（含 `..`）会被直接拒绝。
      </p>
      <div className="mt-3 grid grid-cols-2 gap-4">
        <div>
          <label className={label}>相对目录（留空＝根目录本身）</label>
          <input className={field} value={directory} onChange={(e) => setDirectory(e.target.value)} placeholder="policies/2026" />
        </div>
        <div>
          <label className={label}>操作者署名（回显用，非认证）</label>
          <input className={field} value={actor} onChange={(e) => setActor(e.target.value)} placeholder="ops" />
        </div>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={submit}
          className="rounded-sm border border-ink/30 bg-paper-raised px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-ink hover:text-paper disabled:opacity-40"
        >
          {busy ? "扫描并入库中…" : "扫描目录并入库"}
        </button>
        {!canSubmit && !busy && (
          <span className="text-[11px] text-ink-soft">
            {aclTags.length === 0 ? "请至少勾选一个可见域；" : ""}
            {actor.trim().length === 0 ? "请填写操作者署名；" : ""}
          </span>
        )}
        {error && <span className="text-xs text-brick">失败：{error}</span>}
      </div>

      {receipt && (
        <div className="mt-4 rounded-sm border border-seal/40 bg-seal-soft/40 px-4 py-3">
          <div className="text-sm font-medium text-ink">
            本批入库 {receipt.ingested} 份 · 未入库 {receipt.not_ingested} 份 · 变更事件 {receipt.change_total} 条（删除 {receipt.change_deleted} · 重索引 {receipt.change_reindexed} · 清缓存 {receipt.change_cache_evicted}）
          </div>
          <div className="mt-1 font-mono text-[11px] text-ink-soft">
            根 {receipt.root} · 目标 {receipt.relative} · 可见 {receipt.acl_tags.join("/") || "（空）"} · 操作者 {receipt.actor}
          </div>
          {receipt.delete_scan === "subdir-off" && (
            <p className="mt-2 text-[12px] leading-relaxed text-brick">
              本次只扫子目录，「文件消失」判定未执行（看不见兄弟目录，照样比对位点账会把它们误判成删除）。要按目录真相清理下架，请对根目录跑一次。
            </p>
          )}
          {BUCKETS.map((b) => {
            const rows = receipt[b.key] as DirBucketRow[];
            if (rows.length === 0) return null;
            return (
              <div key={b.key} className="mt-3 border-t border-ink/10 pt-2">
                <div className="text-[12px] font-medium text-ink">
                  {b.label} · {rows.length}
                  <span className="ml-2 font-normal text-ink-soft">{b.note}</span>
                </div>
                <Rows rows={rows} />
              </div>
            );
          })}
          {receipt.ingested > 0 && (
            <button
              type="button"
              onClick={onDone}
              className="mt-3 rounded-sm border border-ink/25 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-ink hover:text-paper"
            >
              去问答验证
            </button>
          )}
        </div>
      )}
    </section>
  );
}
