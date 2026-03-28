import { useMemo } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { useTraceData } from "../../hooks/useTraceData";
import { TraceTree } from "./TraceTree";

export function TraceView() {
  const { selectedSessionId } = useSelection();
  const { data: trace, tree, isLoading, error } = useTraceData(selectedSessionId);

  const traceStartMs = useMemo(() => {
    if (!trace?.timestamp_start) return null;
    try {
      return new Date(trace.timestamp_start).getTime();
    } catch {
      return null;
    }
  }, [trace?.timestamp_start]);

  if (!selectedSessionId) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        select a session
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        loading trace...
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--red)] text-[11px] font-[family-name:var(--font-mono)] px-4">
        error: {error.message}
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-[var(--bg)]">
      {/* Minimal header: just step count */}
      {trace && (
        <div className="flex-none flex items-center justify-end px-3 py-1 border-b border-[var(--border)]">
          <span className="text-[9px] text-[var(--text-dim)] font-[family-name:var(--font-mono)]">
            {trace.steps.length} steps
          </span>
        </div>
      )}

      {/* The trace tree */}
      <div className="flex-1 overflow-hidden">
        <TraceTree tree={tree} traceStartMs={traceStartMs} />
      </div>
    </div>
  );
}
