import { useState } from "react";
import { SessionRow } from "./SessionRow";
import type { SessionStage, SessionListItem } from "../../types/trace";

const STAGE_COLORS: Record<SessionStage, string> = {
  unstaged: "var(--text-muted)",
  staged: "var(--yellow)",
  committed: "var(--green)",
  pushed: "var(--cyan)",
  rejected: "var(--red)",
};

interface StageGroupProps {
  stage: SessionStage;
  sessions: SessionListItem[];
}

export function StageGroup({ stage, sessions }: StageGroupProps) {
  const [collapsed, setCollapsed] = useState(false);
  const color = STAGE_COLORS[stage];

  return (
    <div>
      <button
        onClick={() => setCollapsed((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-1.5 hover:bg-[var(--surface-hover)] transition-colors duration-100 cursor-pointer"
      >
        <span
          className="text-[10px] uppercase tracking-wider font-[family-name:var(--font-mono)]"
          style={{ color }}
        >
          {collapsed ? "+" : "-"} {stage}
        </span>
        <span
          className="text-[9px] font-[family-name:var(--font-mono)] px-1.5 py-0 border"
          style={{ color, borderColor: color }}
        >
          {sessions.length}
        </span>
      </button>
      {!collapsed &&
        sessions.map((s) => <SessionRow key={s.trace_id} session={s} />)}
    </div>
  );
}
