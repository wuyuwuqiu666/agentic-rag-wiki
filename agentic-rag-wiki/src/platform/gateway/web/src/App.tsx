import { useEffect, useMemo, useRef, useState } from "react";
import { ask, getHealth, getScope } from "./api";
import type { Health, SubjectScope } from "./types";
import LeftRail, { DEFAULT_ROLE, type View } from "./components/LeftRail";
import Composer from "./components/Composer";
import AnswerMessage, { type Turn } from "./components/AnswerMessage";
import WikiPanel from "./components/WikiPanel";
import IngestView from "./components/IngestView";
import RetireView from "./components/RetireView";
import RescopeView from "./components/RescopeView";
import WikiReviewView from "./components/WikiReviewView";

let seq = 0;
const nextId = () => `t${++seq}-${Date.now().toString(36)}`;

export default function App() {
  const [view, setView] = useState<View>("chat");
  const [health, setHealth] = useState<Health | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);
  // 知识域不由前端勾选：这里只持有"以哪个角色问"，可见域由后端按画像划分后读回展示（M4-ca）。
  const [role, setRole] = useState<string>(DEFAULT_ROLE);
  const [scope, setScope] = useState<SubjectScope | null>(null);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [activeChunkId, setActiveChunkId] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((e: Error) => setBackendError(e.message));
  }, []);

  useEffect(() => {
    let alive = true;
    setScope(null);
    setScopeError(null);
    setActiveChunkId(null);
    getScope(role)
      .then((s) => alive && setScope(s))
      .catch((e: Error) => alive && setScopeError(e.message));
    return () => {
      alive = false;
    };
  }, [role]);

  const loading = turns.some((t) => t.loading);

  const railTurn = useMemo(
    () => turns.find((t) => t.id === activeTurnId) ?? [...turns].reverse().find((t) => t.answer) ?? null,
    [turns, activeTurnId],
  );

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  async function handleAsk(question: string) {
    const id = nextId();
    setTurns((prev) => [...prev, { id, question, answer: null, loading: true, error: null }]);
    setActiveTurnId(id);
    setActiveChunkId(null);
    try {
      const answer = await ask(question, role, id);
      setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, answer, loading: false } : t)));
    } catch (e) {
      const message = (e as Error).message;
      setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, error: message, loading: false } : t)));
    }
  }

  function activateCitation(turnId: string, chunkId: string) {
    setActiveTurnId(turnId);
    setActiveChunkId(chunkId);
  }

  return (
    <div className="flex h-full w-full overflow-hidden bg-paper text-ink">
      <LeftRail
        role={role}
        onRole={setRole}
        scope={scope}
        scopeError={scopeError}
        health={health}
        error={backendError}
        view={view}
        onView={setView}
      />

      {view === "ingest" ? (
        <main className="min-w-0 flex-1 overflow-hidden">
          <IngestView onDone={() => setView("chat")} />
        </main>
      ) : view === "retire" ? (
        <main className="min-w-0 flex-1 overflow-hidden">
          <RetireView onGoToChat={() => setView("chat")} onGoToReview={() => setView("review")} />
        </main>
      ) : view === "rescope" ? (
        <main className="min-w-0 flex-1 overflow-hidden">
          <RescopeView onGoToReview={() => setView("review")} />
        </main>
      ) : view === "review" ? (
        <main className="min-w-0 flex-1 overflow-hidden">
          <WikiReviewView
            role={role}
            onGoToChat={() => setView("chat")}
            onGoToRetire={() => setView("retire")}
            onGoToRescope={() => setView("rescope")}
          />
        </main>
      ) : (
        <>
          <main className="flex min-w-0 flex-1 flex-col">
            <div ref={threadRef} className="flex-1 overflow-y-auto px-8 py-8">
              {turns.length === 0 ? (
                <div className="mx-auto mt-24 max-w-md text-center">
                  <h1 className="font-serif text-3xl font-semibold tracking-tight text-ink">就依据提问</h1>
                  <p className="mt-3 text-sm leading-relaxed text-ink-soft">
                    答案会引用可核对的出处——已人审的权威页优先，其次是原文片段。
                    点答案下方的引用标记，右侧即联动展开对应正文。
                  </p>
                </div>
              ) : (
                <div className="mx-auto flex max-w-2xl flex-col gap-8">
                  {turns.map((t) => (
                    <AnswerMessage
                      key={t.id}
                      turn={t}
                      activeChunkId={t.id === railTurn?.id ? activeChunkId : null}
                      onActivateCitation={(chunkId) => activateCitation(t.id, chunkId)}
                    />
                  ))}
                </div>
              )}
            </div>
            <Composer onSubmit={handleAsk} disabled={loading} blocked={scope === null} blockedReason={scopeError} />
          </main>

          <WikiPanel
            citations={railTurn?.answer?.citations ?? []}
            role={role}
            activeChunkId={activeChunkId}
            onActivate={setActiveChunkId}
            hasAnswer={!!railTurn?.answer}
          />
        </>
      )}
    </div>
  );
}
