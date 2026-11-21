import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import type { ChunkType, Citation, SourceView } from "../types";
import { getSource } from "../api";

interface Props {
  citation: Citation;
  role: string;
  active: boolean;
  onActivate: () => void;
}

type Load = { kind: "loading" } | { kind: "ready"; data: SourceView } | { kind: "error"; message: string };

// D-2 块型（FR-ING-7/ADR-010）：表格/代码不按散文段落渲染——保留换行与等宽，否则一张表会被压成一行文字。
const TYPE_BADGE: Record<ChunkType, string> = { text: "原文片段", table: "表格块", code: "代码块" };

export default function SourceCard({ citation, role, active, onActivate }: Props) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const ref = useRef<HTMLElement | null>(null);
  const isWiki = citation.authority === "wiki";

  useEffect(() => {
    let alive = true;
    // 只带角色：可见域由后端按画像解析后再读正文，前端不持有权限列表。
    getSource(citation.chunk_id, role)
      .then((data) => alive && setLoad({ kind: "ready", data }))
      .catch((e: Error) => alive && setLoad({ kind: "error", message: e.message }));
    return () => {
      alive = false;
    };
  }, [citation.chunk_id, role]);

  // active＝成为被联动的那张卡：滚动到视野并脉冲一次（对应用户动作的揭示，非散落动画）。
  useEffect(() => {
    if (active && ref.current) {
      ref.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [active]);

  return (
    <motion.article
      ref={ref as React.Ref<HTMLDivElement>}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
      onClick={onActivate}
      className={
        "cursor-pointer rounded-md border-l-[3px] bg-paper-raised p-4 shadow-[0_1px_0_rgba(27,42,58,0.06)] outline-none " +
        (isWiki ? "border-l-seal " : "border-l-slate ") +
        (active ? "link-pulse ring-2 " + (isWiki ? "ring-seal/45" : "ring-slate/45") : "hover:ring-1 hover:ring-ink/10")
      }
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={
            "inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[11px] font-medium " +
            (isWiki ? "bg-seal-soft text-seal" : "bg-slate-soft text-slate")
          }
        >
          {isWiki ? "已人审 · 权威页" : load.kind === "ready" ? TYPE_BADGE[load.data.chunk_type] : "原文片段"}
        </span>
        <span className="font-mono text-[11px] text-ink-soft/70">
          {citation.doc_id}@{citation.doc_version}
        </span>
      </div>

      {citation.breadcrumb.length > 0 && (
        <p className="mt-2 text-[11px] text-ink-soft">
          {citation.breadcrumb.join(" › ")}
          {citation.page_no != null && <span className="text-ink-soft/70"> · 第 {citation.page_no} 页</span>}
        </p>
      )}

      <div className="mt-3 font-serif text-[13.5px] leading-relaxed text-ink">
        {load.kind === "loading" && <span className="text-ink-soft/50">读取出处正文…</span>}
        {load.kind === "error" && (
          <span className="text-brick">正文不可读：{load.message}</span>
        )}
        {load.kind === "ready" &&
          (load.data.chunk_type === "text" ? (
            <p className="line-clamp-6">{load.data.text}</p>
          ) : (
            <pre className="line-clamp-6 overflow-x-auto whitespace-pre rounded-sm bg-paper p-2 font-mono text-[12px] leading-snug">
              {load.data.text}
            </pre>
          ))}
      </div>

      {/* 表格退化成多行组时的父子链：只提示"这是同一张表的第 N 组"，不自动跨块拼正文（正文仍逐块可读）。 */}
      {load.kind === "ready" && load.data.parent_chunk_id && (
        <p className="mt-2 text-[11px] text-ink-soft">
          同表行组 · 父块{" "}
          <span className="font-mono text-[10.5px]">{load.data.parent_chunk_id.split("#").pop()}</span>
        </p>
      )}

      <p className="mt-3 font-mono text-[10.5px] text-ink-soft/55">{citation.chunk_id}</p>
    </motion.article>
  );
}
