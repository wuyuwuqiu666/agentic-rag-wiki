import type { Citation } from "../types";
import SourceCard from "./SourceCard";

interface Props {
  citations: Citation[];
  role: string;
  activeChunkId: string | null;
  onActivate: (chunkId: string) => void;
  hasAnswer: boolean;
}

export default function WikiPanel({ citations, role, activeChunkId, onActivate, hasAnswer }: Props) {
  const wikiCount = citations.filter((c) => c.authority === "wiki").length;
  const sourceCount = citations.length - wikiCount;

  return (
    <section className="flex w-[26rem] shrink-0 flex-col border-l border-rule bg-paper">
      <header className="border-b border-rule px-5 py-4">
        <h2 className="font-serif text-lg font-semibold text-ink">出处联动</h2>
        <p className="mt-1 text-xs text-ink-soft">
          {hasAnswer ? (
            <>
              本条答案 <span className="text-seal">{wikiCount} 个权威页</span> ·{" "}
              <span className="text-slate">{sourceCount} 段原文</span>
              {wikiCount > 0 && "（已人审 wiki 优先，FR-WIKI-5）"}
            </>
          ) : (
            "点击答案中的引用标记，或选择卡片，可定位并展开正文。"
          )}
        </p>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {citations.length === 0 && (
          <div className="mt-10 px-4 text-center text-sm text-ink-soft/70">
            {hasAnswer ? "此答案无可用出处（可能被拒答）。" : "提问后，答案的每条出处会以卡片出现在这里。"}
          </div>
        )}
        {citations.map((c) => (
          <SourceCard
            key={c.chunk_id}
            citation={c}
            role={role}
            active={activeChunkId === c.chunk_id}
            onActivate={() => onActivate(c.chunk_id)}
          />
        ))}
      </div>
    </section>
  );
}
