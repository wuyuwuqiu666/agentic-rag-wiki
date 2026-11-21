import { motion } from "motion/react";
import type { Answer } from "../types";

export interface Turn {
  id: string;
  question: string;
  answer: Answer | null;
  loading: boolean;
  error: string | null;
}

interface Props {
  turn: Turn;
  activeChunkId: string | null;
  onActivateCitation: (chunkId: string) => void;
}

function chipLabels(answer: Answer): { c: Answer["citations"][number]; label: string }[] {
  let w = 0;
  let s = 0;
  return answer.citations.map((c) => {
    const label = c.authority === "wiki" ? `W${++w}` : `S${++s}`;
    return { c, label };
  });
}

export default function AnswerMessage({ turn, activeChunkId, onActivateCitation }: Props) {
  const { answer, loading, error } = turn;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className="space-y-3"
    >
      <div className="text-sm font-medium text-ink-soft">问</div>
      <p className="border-l-2 border-rule pl-3 font-serif text-[15px] text-ink">{turn.question}</p>

      <div className="text-sm font-medium text-ink-soft">答</div>
      {error ? (
        <p className="rounded-sm bg-brick/10 px-3 py-2 text-sm text-brick">检索失败：{error}</p>
      ) : loading || !answer ? (
        <p className="font-serif text-[15px] text-ink-soft/60">检索并核对出处中…</p>
      ) : answer.refused ? (
        <p className="rounded-sm bg-brick/10 px-3 py-2 font-serif text-[15px] text-ink">
          当前身份可见范围内没有找到足够依据，已拒答——无出处的句子不编造。可在左栏调整持有的知识域后重试。
        </p>
      ) : (
        <>
          <p className="font-serif text-[15px] leading-relaxed text-ink">{answer.text}</p>

          {answer.citations.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              <span className="mr-1 text-xs text-ink-soft/70">出处</span>
              {chipLabels(answer).map(({ c, label }) => {
                const isWiki = c.authority === "wiki";
                const active = activeChunkId === c.chunk_id;
                return (
                  <button
                    key={c.chunk_id}
                    type="button"
                    onClick={() => onActivateCitation(c.chunk_id)}
                    title={c.doc_id}
                    className={
                      "rounded-sm border px-2 py-0.5 font-mono text-[11px] transition-all " +
                      (isWiki ? "border-seal/40 " : "border-slate/40 ") +
                      (active
                        ? isWiki
                          ? "bg-seal text-paper"
                          : "bg-slate text-paper"
                        : isWiki
                          ? "bg-seal-soft text-seal hover:bg-seal/20"
                          : "bg-slate-soft text-slate hover:bg-slate/15")
                    }
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 pt-1 text-[11px] text-ink-soft/70">
            <span>置信 {(answer.confidence * 100).toFixed(0)}%</span>
            <span>跳数 {answer.hops}</span>
            {answer.degraded && <span className="rounded-sm bg-seal-soft px-1.5 py-0.5 text-seal">降级路径</span>}
            {answer.trace_id && <span className="font-mono">trace {answer.trace_id.slice(0, 8)}</span>}
          </div>
        </>
      )}
    </motion.div>
  );
}
