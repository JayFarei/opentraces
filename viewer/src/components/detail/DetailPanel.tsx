import { useState } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { useTraceData } from "../../hooks/useTraceData";
import { findNode } from "../../lib/tree";
import { StepDetail } from "./StepDetail";
import { ToolCallDetail } from "./ToolCallDetail";
import { ContextReview } from "./ContextReview";

type DetailTab = "detail" | "review";

export function DetailPanel() {
  const { selectedSessionId, selectedNodeId } = useSelection();
  const { tree, data: trace } = useTraceData(selectedSessionId);
  const [activeTab, setActiveTab] = useState<DetailTab>("detail");

  const node = selectedNodeId ? findNode(tree, selectedNodeId) : null;
  const showToolDetail = node?.toolCall !== undefined;
  const showStepDetail = node?.step !== undefined && !showToolDetail;

  const tabs: DetailTab[] = ["detail", "review"];

  return (
    <div className="h-full flex flex-col bg-[var(--bg)]">
      {/* Tab bar */}
      <div className="flex-none flex items-center border-b border-[var(--border)] px-2">
        {tabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-1 text-[10px] uppercase tracking-wider font-[family-name:var(--font-mono)] transition-colors duration-100 cursor-pointer ${
              activeTab === tab
                ? "text-[var(--accent)] border-b-2 border-b-[var(--accent)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === "detail" && (
          <div className="h-full overflow-y-auto p-3">
            {!selectedSessionId || !node ? (
              <div className="h-full flex flex-col items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)] gap-1">
                <span>click a step in the tree to view details</span>
                <span className="text-[9px] text-[var(--text-dim)]">j/k to navigate, ? for shortcuts</span>
              </div>
            ) : (
              <>
                {showStepDetail && (
                  <StepDetail step={node.step!} security={trace?.security ?? null} />
                )}
                {showToolDetail && (
                  <ToolCallDetail toolCall={node.toolCall!} observation={node.observation ?? null} />
                )}
              </>
            )}
          </div>
        )}

        {activeTab === "review" && <ContextReview />}
      </div>
    </div>
  );
}
