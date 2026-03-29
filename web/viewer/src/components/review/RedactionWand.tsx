import { useState, useCallback, useEffect } from "react";
import { SparklesIcon } from "../icons";

/**
 * Redaction Wand: a mode toggle that lets users select text to redact.
 *
 * When active:
 * - Cursor changes to crosshair across the trace/detail panels
 * - User selects text anywhere in the viewer
 * - A floating popover appears with [redact] and [cancel] buttons
 * - Clicking [redact] sends the selection to the API for redaction
 */

interface RedactionWandProps {
  onRedact: (text: string) => void;
}

export function RedactionWand({ onRedact }: RedactionWandProps) {
  const [active, setActive] = useState(false);
  const [selection, setSelection] = useState<{
    text: string;
    x: number;
    y: number;
  } | null>(null);

  const handleSelectionChange = useCallback(() => {
    if (!active) return;

    const sel = window.getSelection();
    const text = sel?.toString().trim();
    if (!text || text.length < 2) {
      setSelection(null);
      return;
    }

    // Get position for the popover
    const range = sel?.getRangeAt(0);
    if (!range) return;
    const rect = range.getBoundingClientRect();

    setSelection({
      text,
      x: rect.left + rect.width / 2,
      y: rect.top - 10,
    });
  }, [active]);

  useEffect(() => {
    if (!active) {
      setSelection(null);
      return;
    }

    document.addEventListener("mouseup", handleSelectionChange);
    return () => document.removeEventListener("mouseup", handleSelectionChange);
  }, [active, handleSelectionChange]);

  // Add crosshair cursor when active
  useEffect(() => {
    if (active) {
      document.body.style.cursor = "crosshair";
      document.body.classList.add("redaction-wand-active");
    } else {
      document.body.style.cursor = "";
      document.body.classList.remove("redaction-wand-active");
    }
    return () => {
      document.body.style.cursor = "";
      document.body.classList.remove("redaction-wand-active");
    };
  }, [active]);

  const handleRedact = useCallback(() => {
    if (selection) {
      onRedact(selection.text);
      setSelection(null);
      window.getSelection()?.removeAllRanges();
    }
  }, [selection, onRedact]);

  const handleCancel = useCallback(() => {
    setSelection(null);
    window.getSelection()?.removeAllRanges();
  }, []);

  return (
    <>
      {/* Wand toggle button */}
      <button
        onClick={() => {
          setActive((v) => !v);
          setSelection(null);
        }}
        className={`flex items-center gap-1.5 text-[11px] font-[family-name:var(--font-mono)] px-2 py-0.5 border transition-colors duration-100 cursor-pointer ${
          active
            ? "text-[var(--yellow)] border-[var(--yellow)] bg-[color:rgba(234,179,8,0.1)]"
            : "text-[var(--text-muted)] border-[var(--border)] hover:text-[var(--text)] hover:border-[var(--text-muted)]"
        }`}
        title={active ? "Deactivate redaction wand" : "Activate redaction wand: select text to redact"}
      >
        <SparklesIcon size={12} color="currentColor" strokeWidth={2} />
        <span>{active ? "[wand on]" : "[redact]"}</span>
      </button>

      {/* Active indicator bar */}
      {active && (
        <div className="fixed top-0 left-0 right-0 h-[2px] bg-[var(--yellow)] z-50" />
      )}

      {/* Selection popover */}
      {selection && (
        <div
          className="fixed z-50 flex items-center gap-1 bg-[var(--surface-elevated)] border border-[var(--yellow)] px-2 py-1 shadow-lg"
          style={{
            left: `${String(Math.max(10, selection.x - 80))}px`,
            top: `${String(Math.max(10, selection.y - 36))}px`,
          }}
        >
          <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] max-w-[200px] truncate">
            "{selection.text.length > 30 ? selection.text.slice(0, 29) + "\u2026" : selection.text}"
          </span>
          <button
            onClick={handleRedact}
            className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--yellow)] border border-[var(--yellow)] px-1.5 py-0 hover:bg-[color:rgba(234,179,8,0.15)] transition-colors duration-100 cursor-pointer"
          >
            [redact]
          </button>
          <button
            onClick={handleCancel}
            className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] hover:text-[var(--text)] transition-colors duration-100 cursor-pointer"
          >
            x
          </button>
        </div>
      )}
    </>
  );
}
