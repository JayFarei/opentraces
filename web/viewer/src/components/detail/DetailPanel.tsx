import { useSelection } from "../../contexts/SelectionContext";
import { useTraceData } from "../../hooks/useTraceData";
import { findNode } from "../../lib/tree";
import { StepDetail } from "./StepDetail";
import { ToolCallDetail } from "./ToolCallDetail";

export function DetailPanel() {
  const { selectedSessionId, selectedNodeId } = useSelection();
  const { tree, data: trace } = useTraceData(selectedSessionId);

  const node = selectedNodeId ? findNode(tree, selectedNodeId) : null;
  const showToolDetail = node?.toolCall !== undefined;
  const showStepDetail = node?.step !== undefined && !showToolDetail;

  return (
    <div className="h-full flex flex-col bg-[var(--bg)]">
      {/* Detail header */}
      <div className="flex-none flex items-center border-b border-[var(--border)] px-2">
        <span className="px-3 py-1 text-[10px] uppercase tracking-wider font-[family-name:var(--font-mono)] text-[var(--accent)] border-b-2 border-b-[var(--accent)]">
          detail
        </span>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
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
      </div>
    </div>
  );
}
