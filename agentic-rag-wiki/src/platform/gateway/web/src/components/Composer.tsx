import { useState } from "react";

interface Props {
  onSubmit: (question: string) => void;
  disabled: boolean;
  // 可见域由后端划分：拿不到划分结果时不允许提问（fail-loud，而不是"先问着看"）。
  blocked: boolean;
  blockedReason: string | null;
}

export default function Composer({ onSubmit, disabled, blocked, blockedReason }: Props) {
  const [value, setValue] = useState("");

  function send() {
    const q = value.trim();
    if (!q || disabled || blocked) return;
    onSubmit(q);
    setValue("");
  }

  return (
    <div className="border-t border-rule bg-paper-raised px-6 py-4">
      {blocked && (
        <p className="mb-2 text-xs text-brick">
          {blockedReason
            ? `画像没有划出可见知识域，先别提问：${blockedReason}`
            : "正在按画像划分可见知识域……"}
        </p>
      )}
      <div className="flex items-end gap-3">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          rows={1}
          maxLength={512}
          placeholder="就制度、政策或运维手册提问，例如：出差住宿报销标准是什么？"
          className="max-h-40 min-h-[3rem] flex-1 resize-y rounded-sm border border-rule bg-paper px-4 py-3 font-serif text-[15px] leading-relaxed text-ink outline-none placeholder:text-ink-soft/50 focus:border-slate focus:ring-2 focus:ring-slate/25"
        />
        <button
          type="button"
          onClick={send}
          disabled={disabled || !value.trim() || blocked}
          className="rounded-sm bg-ink px-5 py-3 text-sm font-medium text-paper transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
        >
          {disabled ? "检索中" : "提问"}
        </button>
      </div>
      <p className="mt-2 text-[11px] text-ink-soft/70">Enter 发送 · Shift+Enter 换行</p>
    </div>
  );
}
