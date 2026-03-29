import { useState, useCallback } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { useSessionList } from "../../hooks/useSessionList";
import { useReviewActions } from "../../hooks/useReviewActions";
import { CommitDialog } from "../review/CommitDialog";
import { RedactionWand } from "../review/RedactionWand";
import type { SessionStage } from "../../types/trace";

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
  const { selectedSessionId } = useSelection();
  const { data: sessions } = useSessionList();
  const { commit: commitSession, reject, push } = useReviewActions();
  const [showCommitDialog, setShowCommitDialog] = useState(false);

  const handleWandRedact = useCallback((text: string) => {
    // For now, log the redaction. Full API integration when backend supports text-based redaction.
    console.log("[redaction-wand] User selected for redaction:", text);
    // TODO: POST /api/session/<id>/redact-text { text, trace_id }
    alert(`Marked for redaction: "${text.length > 50 ? text.slice(0, 49) + "\u2026" : text}"`);
  }, []);

  const session = sessions?.find((s) => s.trace_id === selectedSessionId);
  if (!session) return null;

  const currentStage: SessionStage = session.stage;
  const traceId = session.trace_id;
  const isBusy =
    commitSession.isPending || reject.isPending || push.isPending;

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
              label="commit"
              color="var(--green)"
              onClick={() => setShowCommitDialog(true)}
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

        {currentStage === "committed" && (
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

      {showCommitDialog && (
        <CommitDialog
          sessions={sessions?.filter((s) => s.stage === "inbox") ?? []}
          onClose={() => setShowCommitDialog(false)}
        />
      )}
    </>
  );
}
