import type { Health, SubjectScope } from "../types";

// 左窄栏：产品身份 + 提问角色 + 后端划分出的可见域（只读）+ 诚实装配徽标。
// 知识域不再由前端勾选：可见域来自 C 区角色聚合画像（GET /api/scope），pre-filter 在后端 App 内执法。
// 因此越权内容在结果里根本不会出现（不是前端藏起来）；切换角色只是换"以谁的画像问"。

export const ROLES = [
  { id: "finance-analyst", label: "财务分析师" },
  { id: "hr-partner", label: "人力伙伴" },
  { id: "release-oncall", label: "发布值班" },
  { id: "kb-auditor", label: "知识库审计（跨域）" },
];

export const DEFAULT_ROLE = ROLES[0].id;

// 可见域标签：后端给的是 acl 标签，这里只做展示用译名，不参与任何判定。
const DOMAIN_LABELS: Record<string, string> = { fin: "财务", hr: "人力", eng: "工程" };

interface Props {
  role: string;
  onRole: (role: string) => void;
  scope: SubjectScope | null;
  scopeError: string | null;
  health: Health | null;
  error: string | null;
  view: View;
  onView: (view: View) => void;
}

export type View = "chat" | "ingest" | "retire" | "rescope" | "review";

const NAV = [
  { key: "chat" as const, label: "问答", hint: "带引用提问" },
  { key: "ingest" as const, label: "知识中心", hint: "投原文或文件入库" },
  { key: "retire" as const, label: "下架与恢复", hint: "墓碑账 · 保留期内可回滚" },
  { key: "rescope" as const, label: "改可见域", hint: "换受众 · 权威页须重署名" },
  { key: "review" as const, label: "权威页审核", hint: "草稿→人审→发布" },
];

export default function LeftRail({ role, onRole, scope, scopeError, health, error, view, onView }: Props) {
  return (
    <aside className="flex w-64 shrink-0 flex-col gap-6 border-r border-ink/40 bg-ink px-5 py-6 text-paper">
      <header>
        <div className="font-serif text-2xl font-semibold tracking-tight">检索室</div>
        <p className="mt-1 text-xs leading-relaxed text-paper/55">
          带引用的企业知识库问答 · 答案与出处联动
        </p>
      </header>

      <nav className="flex flex-col gap-1.5">
        {NAV.map((n) => {
          const on = view === n.key;
          return (
            <button
              key={n.key}
              type="button"
              aria-current={on ? "page" : undefined}
              onClick={() => onView(n.key)}
              className={
                "flex flex-col rounded-sm border px-3 py-2 text-left transition-colors " +
                (on
                  ? "border-seal/60 bg-seal/15 text-paper"
                  : "border-transparent text-paper/60 hover:border-paper/25")
              }
            >
              <span className="text-sm font-medium">{n.label}</span>
              <span className="text-[11px] text-paper/40">{n.hint}</span>
            </button>
          );
        })}
      </nav>

      <section>
        <h2 className="text-[11px] font-medium text-paper/45">提问角色</h2>
        <div className="mt-2 flex flex-col gap-1.5">
          {ROLES.map((r) => {
            const on = role === r.id;
            return (
              <button
                key={r.id}
                type="button"
                aria-pressed={on}
                onClick={() => onRole(r.id)}
                className={
                  "rounded-sm border px-3 py-2 text-left text-sm transition-colors " +
                  (on
                    ? "border-seal/60 bg-seal/15 text-paper"
                    : "border-paper/15 text-paper/60 hover:border-paper/35")
                }
              >
                {r.label}
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <h2 className="text-[11px] font-medium text-paper/45">可见知识域（后端划分）</h2>
        {scopeError ? (
          <p className="mt-2 rounded-sm bg-brick/20 px-3 py-2 text-[11px] leading-relaxed text-paper/90">
            {scopeError}
          </p>
        ) : scope ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {scope.domains.map((d) => (
              <span key={d} className="rounded-sm border border-seal/40 bg-seal/10 px-2 py-1 text-[11px] text-paper/85">
                {DOMAIN_LABELS[d] ?? d}
              </span>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-[11px] text-paper/40">按该角色的聚合画像解析可见域中…</p>
        )}
        <p className="mt-2 text-[11px] leading-relaxed text-paper/40">
          可见域由 C 区角色聚合画像算出，前端不可勾选；画像未建立的角色后端不划分任何域并直接报错。
          角色＝调用侧自报身份（非登录认证），真实按主体归因画像＝鉴权放行档。
        </p>
      </section>

      <section className="mt-auto">
        <h2 className="text-[11px] font-medium text-paper/45">装配状态</h2>
        {error ? (
          <p className="mt-2 rounded-sm bg-brick/20 px-3 py-2 text-xs text-paper/90">
            后端不可达：{error}
          </p>
        ) : health ? (
          <div className="mt-2 flex flex-col gap-1 text-xs text-paper/60">
            <span className="flex items-center gap-2">
              <span className="size-1.5 rounded-full bg-seal" />
              门面在线 · {health.assembly}
            </span>
            <span className="rounded-sm border border-paper/15 px-2 py-1 text-[11px] text-paper/50">
              答案文本＝检索接地的模板回显（{health.quality}），非模型质量档，勿作幻觉率/质量结论。
            </span>
          </div>
        ) : (
          <p className="mt-2 text-xs text-paper/40">连接中…</p>
        )}
      </section>
    </aside>
  );
}
