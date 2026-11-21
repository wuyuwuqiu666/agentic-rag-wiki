import { useCallback, useEffect, useMemo, useState } from "react";
import { listTombstones, restoreTombstones, retireDocument, type ChangeResult, type TombstoneRow } from "../api";
import { domainLabel } from "../domains";

// 下架与恢复（M4-ce）：入库区的生命周期另一半——一份文档怎么从答案里退场，以及退场后还能不能找回来。
// 后端把删除做成"软删"：两路索引（向量 + 词法）与语义缓存一并摘净，正文留在对象库并记一条带保留期的墓碑，
// 所以这里列的不是"回收站清单"而是**可回滚窗口清单**——过窗或正文真丢的条目会如实标成不可恢复，不给假希望。
// 判定与落地都在 IngestPipeline（B0 写侧总闸、B1 批量删除占比、墓碑账）；署名＝调用侧自报，非登录认证。

// 窗口倒计时：人说"还有多久能找回"要的是"天/小时/分钟"，不是 2592000 秒。
function windowLeft(seconds: number): string {
  if (seconds <= 0) return "已超窗";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d} 天 ${h} 小时`;
  if (h > 0) return `${h} 小时 ${m} 分`;
  return `${m} 分`;
}

function stampOf(t: number): string {
  return new Date(t * 1000).toLocaleString("zh-CN", { hour12: false });
}

function rowState(r: TombstoneRow): { label: string; cls: string } {
  if (!r.recoverable) return { label: "超出可回滚窗口", cls: "border-rule bg-paper text-ink-soft" };
  if (!r.has_body) return { label: "正文已不在对象库", cls: "border-brick/50 bg-brick/10 text-ink" };
  return { label: "可恢复", cls: "border-seal/60 bg-seal-soft text-ink" };
}

interface Props {
  onGoToChat: () => void;
  onGoToReview: () => void;
}

export default function RetireView({ onGoToChat, onGoToReview }: Props) {
  const [docId, setDocId] = useState("");
  const [actor, setActor] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [rows, setRows] = useState<TombstoneRow[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ChangeResult | { text: string } | null>(null);

  const reload = useCallback(() => {
    listTombstones()
      .then((v) => {
        setRows(v.items);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(reload, [reload]);

  // 可回滚的排前面（待办在前，与审核台同一取向），同组按下架时间倒序。
  const ordered = useMemo(
    () =>
      [...(rows ?? [])].sort((a, b) => {
        const s = Number(b.recoverable) - Number(a.recoverable);
        return s !== 0 ? s : b.deleted_at - a.deleted_at;
      }),
    [rows],
  );
  // 与后端同一口径（M4-cn⑧）：可回滚面＝窗内**且**正文仍在；窗内但正文已丢的找不回来，不数进"能恢复"的期望值里。
  const recoverableCount = ordered.filter((r) => r.recoverable && r.has_body).length;

  async function retire() {
    setBusy("__retire__");
    setError(null);
    setReceipt(null);
    try {
      setReceipt(await retireDocument(docId.trim(), actor.trim()));
      setDocId("");
      setConfirmed(false);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function restore(id: string | null) {
    setBusy(id ?? "__all__");
    setError(null);
    setReceipt(null);
    try {
      const out = await restoreTombstones(id, actor.trim());
      setReceipt({
        text:
          out.restored > 0
            ? `已回滚 ${out.restored} 条${id ? `（${id}）` : ""}：正文重放回两路索引，问答即可再次命中`
            : `没有回滚任何条目（窗内且正文仍在 ${out.recoverable}、正文已丢 ${out.body_missing}、超窗 ${out.expired_unrecoverable}）`,
      });
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const field = "mt-1 w-full rounded-sm border border-rule bg-paper-raised px-3 py-2 text-sm text-ink outline-none focus:border-slate";
  const label = "text-[11px] font-medium text-ink-soft";
  const canRetire = docId.trim().length > 0 && actor.trim().length > 0 && confirmed && busy === null;

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col overflow-y-auto px-8 py-8">
      <header className="mb-6">
        <h1 className="font-serif text-2xl font-semibold tracking-tight text-ink">知识中心 · 下架与恢复</h1>
        <p className="mt-1 text-xs leading-relaxed text-ink-soft">
          下架＝从问答里退场，不是物理销毁：向量与词法两路索引连同语义缓存一并摘净，正文留在对象库并记一条墓碑，
          保留期内可一键回滚。过窗或正文真丢的条目会如实标为不可恢复——这里不给"应该还能找回"的假希望。
        </p>
      </header>

      <section className="rounded-sm border border-rule bg-paper-raised px-5 py-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={label}>要下架的文档 ID</label>
            <input className={field} value={docId} onChange={(e) => setDocId(e.target.value)} placeholder="expense-2026" />
          </div>
          <div>
            <label className={label}>署名（谁下的架；自报身份，非登录）</label>
            <input className={field} value={actor} onChange={(e) => setActor(e.target.value)} placeholder="kb-owner" />
          </div>
        </div>
        <label className="mt-3 flex items-start gap-2 text-[12px] leading-relaxed text-ink">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="mt-0.5 size-3.5 accent-brick"
          />
          <span>
            确认下架该文档的全部版本：它即刻从检索中消失；引用它的已发布权威页要<b>跑一轮编译</b>才被标为「依据已失效」并撤出优先引用（一轮异步窗）。
          </span>
        </label>
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            disabled={!canRetire}
            onClick={retire}
            className="rounded-sm bg-brick px-4 py-2 text-sm font-medium text-paper transition-opacity disabled:opacity-40"
          >
            {busy === "__retire__" ? "下架中…" : "下架"}
          </button>
          {!canRetire && busy === null && (
            <span className="text-[11px] text-ink-soft">
              {actor.trim().length === 0 ? "请填写署名；" : ""}
              {docId.trim().length === 0 ? "请填写文档 ID；" : ""}
              {!confirmed ? "需勾选确认后才可执行；" : ""}
            </span>
          )}
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-ink-soft">
          这条动作走的是变更流，不绕过任何闸：写侧总闸拉闸时返回 503；批量删除占比超限时同样按 B1 判停（那不是失败，是门在拦）。
        </p>
      </section>

      {receipt && "hint" in receipt && (
        <div className="mt-5 rounded-sm border border-seal/40 bg-seal-soft/40 px-4 py-3 text-sm text-ink">
          <div className="font-medium">
            已受理 · {receipt.doc_id}（下架 {receipt.deleted} 块 · 清缓存 {receipt.cache_evicted} 条）
          </div>
          <div className="mt-1 text-[12px] text-ink-soft">{receipt.hint}</div>
          {receipt.recoverable === 0 && <div className="mt-1 text-[12px] text-brick">本条未进可恢复账，不做恢复承诺。</div>}
        </div>
      )}
      {receipt && "text" in receipt && (
        <div className="mt-5 rounded-sm border border-seal/40 bg-seal-soft/40 px-4 py-3 text-sm text-ink">{receipt.text}</div>
      )}
      {error && (
        <div className="mt-5 rounded-sm border border-brick/50 bg-brick/10 px-4 py-3 text-sm text-ink">
          未执行：{error}
        </div>
      )}

      <section className="mt-8 flex min-h-0 flex-1 flex-col">
        <div className="flex items-baseline justify-between">
          <h2 className="font-serif text-lg font-semibold tracking-tight text-ink">
            墓碑账 {rows ? `· ${ordered.length} 条` : ""}
          </h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={reload}
              className="rounded-sm border border-rule px-3 py-1.5 text-xs text-ink-soft transition-colors hover:border-slate hover:text-ink"
            >
              刷新
            </button>
            {recoverableCount > 0 && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => restore(null)}
                className="rounded-sm border border-ink/25 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-ink hover:text-paper disabled:opacity-40"
              >
                全部恢复（{recoverableCount}）
              </button>
            )}
          </div>
        </div>

        {!rows ? (
          <p className="mt-3 text-xs text-ink-soft">读取墓碑账中…</p>
        ) : ordered.length === 0 ? (
          <p className="mt-3 text-xs leading-relaxed text-ink-soft">
            当前没有下架记录。下架一份文档后，它会带着保留期出现在这里，直到被回滚或超出窗口。
          </p>
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {ordered.map((r) => {
              const st = rowState(r);
              const key = `${r.doc_id}/${r.version}`;
              return (
                <li key={key} className="rounded-sm border border-rule bg-paper-raised px-4 py-3">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="font-mono text-[13px] text-ink">{key}</span>
                    <span className={"rounded-sm border px-2 py-0.5 text-[11px] " + st.cls}>{st.label}</span>
                    <span className="text-[11px] text-ink-soft">
                      域 {domainLabel(r.domain)} · {r.chunk_ids.length} 块
                    </span>
                    <span className="ml-auto text-[11px] text-ink-soft">保留期剩 {windowLeft(r.seconds_left)}</span>
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-ink-soft/80">下架于 {stampOf(r.deleted_at)}</div>
                  <div className="mt-2 flex items-center gap-2">
                    {r.recoverable ? (
                      <button
                        type="button"
                        disabled={busy !== null || actor.trim().length === 0}
                        onClick={() => restore(r.doc_id)}
                        className="rounded-sm border border-ink/25 px-3 py-1 text-xs font-medium text-ink transition-colors hover:bg-ink hover:text-paper disabled:opacity-40"
                      >
                        {busy === r.doc_id ? "恢复中…" : "恢复到检索"}
                      </button>
                    ) : (
                      <span className="text-[11px] text-ink-soft">超出窗口后不再提供恢复（真 30 天保留与跨进程账实＝企业档 PG）</span>
                    )}
                    {r.recoverable && !r.has_body && (
                      <span className="text-[11px] text-brick">正文已不在对象库：即便恢复也不会找回内容</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-3 text-[11px] text-ink-soft">
          <button
            type="button"
            onClick={onGoToChat}
            className="rounded-sm border border-ink/25 px-3 py-1.5 font-medium text-ink transition-colors hover:bg-ink hover:text-paper"
          >
            去问答验证
          </button>
          <button
            type="button"
            onClick={onGoToReview}
            className="rounded-sm border border-rule px-3 py-1.5 font-medium text-ink-soft transition-colors hover:border-slate hover:text-ink"
          >
            去审核台撤块
          </button>
          <span>下架＝摘依据：引用它的已发布权威页要<b>跑一轮编译</b>才标脏撤块（一轮异步窗），撤块后须重新署名发布。</span>
          <span className="w-full">恢复＝把正文重放回两路索引（同分块 id 幂等复现），不是重新灌一份新内容。</span>
        </div>
      </section>
    </div>
  );
}
