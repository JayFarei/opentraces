import { useState } from "react";
import { TraceRow } from "./SessionRow";
import type { TraceStage, TraceListItem } from "../../types/trace";

const STAGE_COLORS: Record<TraceStage, string> = {
  inbox: "var(--yellow)",
  staged: "var(--green)",
  pushed: "var(--cyan)",
  rejected: "var(--red)",
};

const STAGE_LABELS: Record<TraceStage, string> = {
  inbox: "Inbox",
  staged: "Staged",
  pushed: "Pushed",
  rejected: "Rejected",
};

interface StageGroupProps {
  stage: TraceStage;
  traces: TraceListItem[];
}

export function StageGroup({ stage, traces }: StageGroupProps) {
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
          {collapsed ? "+" : "-"} {STAGE_LABELS[stage]}
        </span>
        <span
          className="text-[9px] font-[family-name:var(--font-mono)] px-1.5 py-0 border"
          style={{ color, borderColor: color }}
        >
          {traces.length}
        </span>
      </button>
      {!collapsed &&
        traces.map((t) => <TraceRow key={t.trace_id} trace={t} />)}
    </div>
  );
}
