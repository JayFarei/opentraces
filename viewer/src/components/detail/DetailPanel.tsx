import { useSelection } from "../../contexts/SelectionContext";
import { useTraceData } from "../../hooks/useTraceData";
import { findNode } from "../../lib/tree";
import { StepDetail } from "./StepDetail";
import { ToolCallDetail } from "./ToolCallDetail";

export function DetailPanel() {
  const { selectedSessionId, selectedNodeId } = useSelection();
  const { tree, data: trace } = useTraceData(selectedSessionId);

  if (!selectedSessionId || !selectedNodeId) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)] gap-1">
        <span>click a step in the tree to view details</span>
        <span className="text-[9px] text-[var(--text-dim)]">j/k to navigate, ? for shortcuts</span>
      </div>
    );
  }

  const node = findNode(tree, selectedNodeId);
  if (!node) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        node not found
      </div>
    );
  }

  // Tool nodes from merged steps have both step and toolCall
  const showToolDetail = node.toolCall !== undefined;
  const showStepDetail = node.step !== undefined && !showToolDetail;

  return (
    <div className="h-full overflow-y-auto bg-[var(--bg)] p-3">
      {showStepDetail && (
        <StepDetail step={node.step!} security={trace?.security ?? null} />
      )}
      {showToolDetail && (
        <ToolCallDetail toolCall={node.toolCall!} observation={node.observation ?? null} />
      )}
    </div>
  );
}
