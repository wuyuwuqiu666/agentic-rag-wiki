import { useCallback, useEffect, useMemo, useState } from "react";
import { compileWiki, listWikiPages, reviewWiki, type ReviewAction } from "../api";
import type { WikiPageStatus, WikiPageView } from "../types";

// 权威页审核台（M4-cb）：草稿不会自己成为权威——先有人提交人审，再有人署名通过。
// 迁移合法性与署名要求都在后端状态机（wiki/service/review.py）执法，这里只列合法动作并把拒绝原因原样显示。
// 页清单带正文，因此可见域由后端按当前角色的画像过滤（pre-filter）：越权域的页根本不出现，前端不持有权限列表。

const STATUS_META: Record<WikiPageStatus, { label: string; cls: string }> = {
  draft: { label: "草稿", cls: "border-slate/50 bg-slate-soft text-ink" },
  in_review: { label: "待人审", cls: "border-ink/45 bg-paper-raised text-ink" },
  published: { label: "已发布 · 权威页", cls: "border-seal/60 bg-seal-soft text-ink" },
  stale: { label: "源已变更 · 待重审", cls: "border-brick/50 bg-brick/10 text-ink" },
  retracted: { label: "依据已失效", cls: "border-rule bg-paper text-ink-soft" },
};

// 待办优先排（不按字母，按"接下来该做什么"）：待人审 → 草稿 → 源已变更，已发布/失效沉底。
const PENDING: WikiPageStatus[] = ["in_review", "draft", "stale"];
const rank = (s: WikiPageStatus) => {
  const i = PENDING.indexOf(s);
  return i === -1 ? PENDING.length : i;
};

interface Props {
  role: string;
  onGoToChat: () => void;
  onGoToRetire: () => void;
  onGoToRescope: () => void;
}

export default function WikiReviewView({ role, onGoToChat, onGoToRetire, onGoToRescope }: Props) {
  const [pages, setPages] = useState<WikiPageView[] | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ pageId: string; text: string } | null>(null);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());

  const reload = useCallback(() => {
    listWikiPages(role)
      .then((p) => {
        setPages(p);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [role]);

  useEffect(reload, [reload]);

  const pendingCount = useMemo(() => (pages ?? []).filter((p) => PENDING.includes(p.status)).length, [pages]);

  // sort 在同状态内保持稳定，故并列项维持后端返回顺序。
  const ordered = useMemo(() => [...(pages ?? [])].sort((a, b) => rank(a.status) - rank(b.status)), [pages]);

  async function compile() {
    setBusy("__compile__");
    setError(null);
    try {
      const { new_drafts } = await compileWiki();
      reload();
      setError(new_drafts === 0 ? "本轮没有新草稿：多数入库文档未过 triage 入选门（制度/规范/runbook/圈定/长文才候选）。" : null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function act(pageId: string, action: ReviewAction) {
    setBusy(pageId);
    setError(null);
    setNotice(null);
    try {
      const res = await reviewWiki(pageId, action, role, reviewer.trim());
      setNotice({
        pageId,
        text:
          action === "approve"
            ? `已由 ${res.page.reviewer} 署名发布，回写 ${res.indexed} 个权威块进索引——问答里该域引用即以 W 优先。`
            : action === "submit"
              ? "已提交人审：现在需要有审阅人署名通过或打回。"
              : "已打回草稿：改动后重新提交人审。",
      });
      reload();
    } catch (e) {
      setError((e as Error).message); // 人审门/写侧拉闸的原文案照实显示，不粉饰
    } finally {
      setBusy(null);
    }
  }

  function toggle(pageId: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(pageId)) next.delete(pageId);
      else next.add(pageId);
      return next;
    });
  }

  const signed = reviewer.trim().length > 0;

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col overflow-y-auto px-8 py-8">
      <header className="mb-5">
        <h1 className="font-serif text-2xl font-semibold tracking-tight text-ink">权威页审核</h1>
        <p className="mt-1 text-xs leading-relaxed text-ink-soft">
          入库只进原文层（source）；要成为答案里优先引用的权威页（wiki），必须经这里：编译出草稿 → 提交人审 → 审阅人署名通过。
          草稿不可直接发布、空署名不可发布，这条门在后端状态机执法，界面无法绕过。
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3 rounded-sm border border-rule bg-paper-raised px-4 py-3">
        <div className="min-w-52 flex-1">
          <label className="text-[11px] font-medium text-ink-soft" htmlFor="reviewer">
            审阅署名（发布必填，随审计留痕）
          </label>
          <input
            id="reviewer"
            className="mt-1 w-full rounded-sm border border-rule bg-paper px-3 py-2 text-sm outline-none focus:border-slate"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="如 fin-owner"
          />
        </div>
        <button
          type="button"
          onClick={compile}
          disabled={busy !== null}
          className="rounded-sm border border-ink/25 px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-ink hover:text-paper disabled:opacity-40"
        >
          {busy === "__compile__" ? "编译中…" : "跑一轮编译"}
        </button>
        <span className="text-[11px] text-ink-soft">
          {pages === null ? "读取页清单…" : `${pages.length} 页 · 待办 ${pendingCount}`}
        </span>
      </div>

      {error && <p className="mt-3 rounded-sm bg-brick/10 px-3 py-2 text-xs leading-relaxed text-brick">{error}</p>}

      <div className="mt-5 flex flex-col gap-4">
        {ordered.map((p) => {
          const meta = STATUS_META[p.status];
          const isOpen = open.has(p.page_id);
          const busyHere = busy === p.page_id;
          return (
            <article key={p.page_id} className={"rounded-sm border px-4 py-3 " + (p.status === "published" ? "border-seal/50" : "border-rule")}>
              <div className="flex flex-wrap items-center gap-2">
                <span className={"rounded-sm border px-2 py-0.5 text-[11px] " + meta.cls}>{meta.label}</span>
                <h2 className="font-serif text-lg font-semibold leading-tight text-ink">{p.title || p.page_id}</h2>
                <span className="ml-auto font-mono text-[11px] text-ink-soft">{p.page_id} · v{p.version}</span>
              </div>
              <p className="mt-1.5 text-[11px] leading-relaxed text-ink-soft">
                入选依据：{p.triage_reason || "—"} · 源文档{" "}
                <span className="font-mono">{p.source_doc_ids.join(", ") || "—"}</span> · 可见域 {p.acl_tags.join("/") || "（无）"}
                {p.reviewer ? ` · 审阅人 ${p.reviewer}` : ""}
              </p>

              <button
                type="button"
                onClick={() => toggle(p.page_id)}
                className="mt-2 text-[11px] text-slate underline decoration-slate/40 underline-offset-2"
              >
                {isOpen ? "收起正文" : `看正文与依据（${p.sections.length} 节）`}
              </button>
              {isOpen &&
                p.sections.map((s) => (
                  <div key={s.heading + p.page_id} className="mt-2 border-l-2 border-slate/40 pl-3">
                    <div className="text-[11px] font-medium text-ink">{s.heading}</div>
                    <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed text-ink-soft">{s.body}</p>
                    <div className="mt-1 font-mono text-[11px] text-slate">{s.source_chunk_ids.join(" · ")}</div>
                  </div>
                ))}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                {(p.status === "draft" || p.status === "stale") && (
                  <Action busy={busyHere} onClick={() => act(p.page_id, "submit")}>
                    提交人审
                  </Action>
                )}
                {p.status === "in_review" && (
                  <>
                    <Action busy={busyHere} onClick={() => act(p.page_id, "approve")} primary disabled={!signed} title={signed ? "" : "先填审阅署名"}>
                      署名通过并发布
                    </Action>
                    <Action busy={busyHere} onClick={() => act(p.page_id, "reject")}>
                      打回重写
                    </Action>
                    {!signed && <span className="text-[11px] text-brick">缺署名：发布门不会放行</span>}
                  </>
                )}
                {p.status === "published" && (
                  <span className="text-[11px] text-ink-soft">
                    已生效。源文档大改会在下轮编译自动标脏、源删除则标记依据已失效——无需手工撤旧。
                  </span>
                )}
                {p.status === "retracted" && <span className="text-[11px] text-ink-soft">依据已失效，不再作为权威引用；需重编译并重新过审。</span>}
                {notice?.pageId === p.page_id && (
                  <span className="text-[11px] text-ink">
                    {notice.text}
                    {p.status === "published" && (
                      <button
                        type="button"
                        onClick={onGoToChat}
                        className="ml-2 rounded-sm border border-ink/25 px-2 py-0.5 text-[11px] transition-colors hover:bg-ink hover:text-paper"
                      >
                        去问答验证
                      </button>
                    )}
                  </span>
                )}
              </div>
            </article>
          );
        })}

        {pages !== null && pages.length === 0 && (
          <div className="rounded-sm border border-dashed border-rule px-4 py-8 text-center">
            <p className="font-serif text-base text-ink">这里还没有你可见的页</p>
            <p className="mx-auto mt-2 max-w-md text-[12px] leading-relaxed text-ink-soft">
              先在「知识中心」入库一篇制度/规范类文档，再点上面的「跑一轮编译」生成草稿；
              若入库的是会议纪要或短文，triage 不会选它沉淀——这是入选门在起作用，不是出错。
            </p>
          </div>
        )}
      </div>

      <div className="mt-6 rounded-sm border border-rule bg-paper-raised px-4 py-3">
        <p className="text-[12px] leading-relaxed text-ink">
          已发布页要撤回＝让它的<strong className="font-medium">源失效</strong>（下架或改可见域）后跑一轮编译，
          页转「依据已失效」并撤出优先引用；审核台没有独立的「撤发布」按钮——发布写进索引的块由谁撤、怎么撤，
          都跟着源走，不在这里另开一条只有界面能看到的口径。
          改了某源的可见域后，这里能看到它的页转「源已变更 · 待重审」，那一步正是把旧受众的权威块撤下。
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onGoToRescope}
            className="rounded-sm border border-ink/25 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-ink hover:text-paper"
          >
            改可见域
          </button>
          <button
            type="button"
            onClick={onGoToRetire}
            className="rounded-sm border border-ink/25 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-ink hover:text-paper"
          >
            下架与恢复
          </button>
        </div>
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-ink-soft">
        诚实边界：署名＝调用侧自报（本层不实现登录认证，也不判"谁有审核权"），作者与审阅人分离的跨人审批＝治理放行档；
        发布即把回写块写进索引，与摄取同受 B0 写侧总闸约束（拉闸期后端返 503，界面上就报原文原因）。
      </p>
    </div>
  );
}

function Action({
  children,
  onClick,
  busy,
  disabled,
  primary,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  busy: boolean;
  disabled?: boolean;
  primary?: boolean;
  title?: string;
}) {
  const base = "rounded-sm px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40";
  const skin = primary
    ? "bg-ink text-paper hover:bg-seal"
    : "border border-ink/25 text-ink hover:bg-ink hover:text-paper";
  return (
    <button type="button" title={title} disabled={busy || disabled} onClick={onClick} className={base + " " + skin}>
      {busy ? "处理中…" : children}
    </button>
  );
}
