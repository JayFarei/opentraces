import { useState } from "react";

interface ReasoningBlockProps {
  content: string;
}

export function ReasoningBlock({ content }: ReasoningBlockProps) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="bg-[var(--bg-alt)] border-l-[3px] border-l-[color:var(--accent)] border-l-opacity-60">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left px-2 py-1 text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] hover:text-[var(--text)] cursor-pointer"
      >
        [{expanded ? "collapse" : "expand"} reasoning]
      </button>
      {expanded && (
        <div className="px-3 pb-2 text-[12px] font-[family-name:var(--font-mono)] text-[var(--text-secondary)] italic whitespace-pre-wrap max-h-[200px] overflow-y-auto">
          {content}
        </div>
      )}
    </div>
  );
}
