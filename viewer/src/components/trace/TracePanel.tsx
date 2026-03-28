import { useState, useMemo } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { useTraceData } from "../../hooks/useTraceData";
import { TraceTree } from "./TraceTree";
import { ContextFlow } from "./ContextFlow";

type Tab = "tree" | "context" | "search";

/** @deprecated Use TraceView instead. Kept for backward compatibility. */
export function TracePanel() {
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
  const [activeTab, setActiveTab] = useState<Tab>("tree");

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

  const tabs: Tab[] = ["tree", "context", "search"];

  return (
    <div className="h-full flex flex-col bg-[var(--bg)]">
      <div className="flex items-center border-b border-[var(--border)] px-2">
        {tabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-1.5 text-[10px] uppercase tracking-wider font-[family-name:var(--font-mono)] transition-colors duration-100 cursor-pointer ${
              activeTab === tab
                ? "text-[var(--accent)] border-b border-b-[var(--accent)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]"
            }`}
          >
            {tab}
          </button>
        ))}
        {trace && (
          <span className="ml-auto text-[9px] text-[var(--text-dim)] font-[family-name:var(--font-mono)]">
            {trace.steps.length} steps
          </span>
        )}
      </div>

      <div className="flex-1 overflow-hidden">
        {activeTab === "tree" && <TraceTree tree={tree} traceStartMs={traceStartMs} />}
        {activeTab === "context" && <ContextFlow tree={tree} />}
        {activeTab === "search" && (
          <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
            search
          </div>
        )}
      </div>
    </div>
  );
}
