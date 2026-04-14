import { useState, useCallback } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { useTraceList } from "../../hooks/useTraceList";
import { useReviewActions } from "../../hooks/useReviewActions";
import { AddDialog } from "../review/AddDialog";
import { RedactionWand } from "../review/RedactionWand";
import type { TraceStage } from "../../types/trace";

function ActionButton({
  label,
  color,
  onClick,
  disabled,
}: {
  label: string;
  color: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`text-[11px] font-[family-name:var(--font-mono)] px-2 py-0.5 border transition-colors duration-100 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed`}
      style={{ color, borderColor: color }}
    >
      [{label}]
    </button>
  );
}

export function ReviewBar() {
  const { selectedTraceId } = useSelection();
  const { data: traces } = useTraceList();
  const { add, reject, push } = useReviewActions();
  const [showAddDialog, setShowAddDialog] = useState(false);

  const handleWandRedact = useCallback((text: string) => {
    // For now, log the redaction. Full API integration when backend supports text-based redaction.
    console.log("[redaction-wand] User selected for redaction:", text);
    // TODO: POST /api/trace/<id>/redact-text { text, trace_id }
    alert(`Marked for redaction: "${text.length > 50 ? text.slice(0, 49) + "\u2026" : text}"`);
  }, []);

  const trace = traces?.find((s) => s.trace_id === selectedTraceId);
  if (!trace) return null;

  const currentStage: TraceStage = trace.stage;
  const traceId = trace.trace_id;
  const isBusy = add.isPending || reject.isPending || push.isPending;

  return (
    <>
      <div className="flex items-center gap-3 px-4 py-2 border-t border-[var(--border)] bg-[var(--bg-alt)]">
        {/* Redaction wand - always available */}
        <RedactionWand onRedact={handleWandRedact} />

        <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mr-2">
          {currentStage}
        </span>

        {currentStage === "inbox" && (
          <>
            <ActionButton
              label="add"
              color="var(--green)"
              onClick={() => setShowAddDialog(true)}
              disabled={isBusy}
            />
            <ActionButton
              label="reject"
              color="var(--red)"
              onClick={() => reject.mutate(traceId)}
              disabled={isBusy}
            />
          </>
        )}

        {currentStage === "staged" && (
          <ActionButton
            label="push"
            color="var(--accent)"
            onClick={() => push.mutate(undefined)}
            disabled={isBusy}
          />
        )}

        {currentStage === "pushed" && (
          <span className="text-[11px] text-[var(--cyan)]">
            pushed to HuggingFace Hub
          </span>
        )}

        {currentStage === "rejected" && (
          <span className="text-[11px] text-[var(--red)]">rejected</span>
        )}
      </div>

      {showAddDialog && (
        <AddDialog
          traces={traces?.filter((s) => s.stage === "inbox") ?? []}
          onClose={() => setShowAddDialog(false)}
        />
      )}
    </>
  );
}
