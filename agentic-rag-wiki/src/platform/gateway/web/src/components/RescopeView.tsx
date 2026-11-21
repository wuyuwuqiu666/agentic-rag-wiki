import { useState } from "react";
import { rescopeDocument, type RescopeReceipt } from "../api";
import { DOMAINS, domainLabel } from "../domains";

// 知识中心 · 改可见域（M4-ci，FR-ING-5）：一份已入库文档换受众，是把变更流的后半段给到界面上。
// 后端一次改四件事（效应②源块 ACL 重写走两路索引+清单、③按前后域并集清语义缓存、④fan-out 令受影响权威页下一轮编译标脏撤块），
// 前端只显示回执里逐条给出的账，一个数都不自己算、一个字都不替后端圆场。
// 关键口径：受众收紧**分两段**——源块即刻、该源已发布的权威块要到下一轮编译才撤，撤下后须**重新署名发布**才回索引
// （=受众变更由人签字，不自动复权）。所以本页明写窗口，不写"改完即生效"。
// 效应①（PG 权限行）属真实双存储=放行项②，回执 effects_pending 里如实挂着，不伪称已改。

interface Props {
  onGoToReview: () => void;
}

export default function RescopeView({ onGoToReview }: Props) {
  const [docId, setDocId] = useState("");
  const [actor, setActor] = useState("");
  const [before, setBefore] = useState<string[]>([]);
  const [after, setAfter] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<RescopeReceipt | null>(null);

  function toggle(which: "before" | "after", tag: string) {
    const set = which === "before" ? setBefore : setAfter;
    set((cur) => (cur.includes(tag) ? cur.filter((t) => t !== tag) : [...cur, tag]));
  }

  const canSubmit = docId.trim().length > 0 && actor.trim().length > 0 && confirmed && busy === false;

  async function submit() {
    setBusy(true);
    setError(null);
    setReceipt(null);
    try {
      setReceipt(await rescopeDocument(docId.trim(), before, after, actor.trim()));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-sm border border-rule bg-paper-raised px-3 py-2 text-sm text-ink outline-none focus:border-slate";
  const label = "text-[11px] font-medium text-ink-soft";

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col overflow-y-auto px-8 py-8">
      <header className="mb-6">
        <h1 className="font-serif text-2xl font-semibold tracking-tight text-ink">知识中心 · 改可见域</h1>
        <p className="mt-1 text-xs leading-relaxed text-ink-soft">
          换受众＝变更流的一种，和下架走同一道门：后端把源块在向量与词法两路索引、以及清单里一并重写，
          再按新旧域的并集清掉语义缓存。问答立刻按新域过滤——但<b>该源已发布的那张权威块仍按旧受众可读，直到下一轮编译被标脏撤块</b>。
          撤下后要重新署名发布才回索引＝受众变更由人签字，系统不自动复权。
        </p>
      </header>

      <section className="rounded-sm border border-rule bg-paper-raised px-5 py-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={label}>文档 ID</label>
            <input className={field} value={docId} onChange={(e) => setDocId(e.target.value)} placeholder="expense-2026" />
          </div>
          <div>
            <label className={label}>署名（谁改的；自报身份，非登录）</label>
            <input className={field} value={actor} onChange={(e) => setActor(e.target.value)} placeholder="kb-owner" />
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4">
          <div>
            <span className={label}>改前的可见域（留空＝本次不声明旧状态）</span>
            <div className="mt-2 flex gap-2">
              {DOMAINS.map((d) => {
                const on = before.includes(d.tag);
                return (
                  <button
                    key={d.tag}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggle("before", d.tag)}
                    className={
                      "rounded-sm border px-3 py-1.5 text-sm transition-colors " +
                      (on ? "border-slate bg-paper text-ink" : "border-rule text-ink-soft hover:border-slate")
                    }
                  >
                    {d.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <span className={label}>改后的可见域（清空全部＝对该文档收回所有域，须显式一个都不选）</span>
            <div className="mt-2 flex gap-2">
              {DOMAINS.map((d) => {
                const on = after.includes(d.tag);
                return (
                  <button
                    key={d.tag}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggle("after", d.tag)}
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
        </div>

        <label className="mt-4 flex items-start gap-2 text-[12px] leading-relaxed text-ink">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="mt-0.5 size-3.5 accent-brick"
          />
          <span>
            确认改域：源块即刻换域；若该源有已发布权威页，其块仍在旧受众下可读，
            直到我跑一轮编译（「权威页审核」页）把该页标脏撤块，之后须重新署名发布。
          </span>
        </label>

        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            disabled={!canSubmit}
            onClick={submit}
            className="rounded-sm bg-brick px-4 py-2 text-sm font-medium text-paper transition-opacity disabled:opacity-40"
          >
            {busy ? "改域中…" : "提交改域"}
          </button>
          {!canSubmit && busy === false && (
            <span className="text-[11px] text-ink-soft">
              {docId.trim().length === 0 ? "请填写文档 ID；" : ""}
              {actor.trim().length === 0 ? "请填写署名；" : ""}
              {!confirmed ? "需勾选确认后才可执行；" : ""}
            </span>
          )}
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-ink-soft">
          这条动作不绕过任何闸：写侧总闸拉闸时 503（改域＝重写索引，属写侧）；「改后」一个域都不选是合法声明（收回全部可见域），
          但要在下面勾明「确认」才提交——不收缺省，避免误触把文档改成谁也看不见。
        </p>
      </section>

      {receipt && (
        <section className="mt-5 rounded-sm border border-seal/40 bg-seal-soft/40 px-4 py-3 text-sm text-ink">
          <div className="font-medium">
            已受理 · {receipt.doc_id}：源块重写 {receipt.acl_rewritten} 块 · 清缓存 {receipt.cache_evicted} 条
            {receipt.acl_pending > 0 && <span className="text-brick"> · 未能重写 {receipt.acl_pending} 块</span>}
          </div>
          <div className="mt-1 text-[12px] text-ink-soft">
            域变化：{before.length > 0 ? before.map(domainLabel).join("、") : "（未声明旧状态）"} →{" "}
            {after.length > 0 ? after.map(domainLabel).join("、") : "（收回全部可见域）"}
          </div>
          {receipt.acl_pending > 0 && (
            <div className="mt-1 text-[12px] text-brick">
              这 {receipt.acl_pending} 块拉不到正文（无登记信封或指针缺失），未重写、留在 B5 背离债里——不是"已经改好"。
              处置＝按 §10 恢复该源正文后重跑，或先下架该文档。
            </div>
          )}
          <ul className="mt-2 flex flex-col gap-1 text-[12px] text-ink-soft">
            {receipt.effects_done.map((e) => (
              <li key={e}>已兑现：{e}</li>
            ))}
            {receipt.effects_pending.map((e) => (
              <li key={e} className="text-brick">
                待兑现：{e}
              </li>
            ))}
          </ul>
          <div className="mt-2 text-[12px] leading-relaxed text-ink-soft">{receipt.hint}</div>
          <button
            type="button"
            onClick={onGoToReview}
            className="mt-3 rounded-sm border border-ink/25 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-ink hover:text-paper"
          >
            去跑一轮编译关掉这个窗
          </button>
        </section>
      )}
      {error && <div className="mt-5 rounded-sm border border-brick/50 bg-brick/10 px-4 py-3 text-sm text-ink">未执行：{error}</div>}
    </div>
  );
}
