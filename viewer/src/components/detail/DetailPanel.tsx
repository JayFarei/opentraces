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
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        select a step to view details
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

  return (
    <div className="h-full overflow-y-auto bg-[var(--bg)] p-3">
      {node.step && <StepDetail step={node.step} security={trace?.security ?? null} />}
      {node.toolCall && !node.step && (
        <ToolCallDetail toolCall={node.toolCall} observation={node.observation ?? null} />
      )}
    </div>
  );
}
