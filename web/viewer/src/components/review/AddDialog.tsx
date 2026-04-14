import { useState } from "react";
import { useReviewActions } from "../../hooks/useReviewActions";
import type { TraceListItem } from "../../types/trace";

interface AddDialogProps {
  traces: TraceListItem[];
  onClose: () => void;
}

export function AddDialog({ traces, onClose }: AddDialogProps) {
  const { add } = useReviewActions();
  const [selected, setSelected] = useState<Set<string>>(
    new Set(traces.map((s) => s.trace_id)),
  );
  const [message, setMessage] = useState(() => {
    const descs = traces
      .slice(0, 3)
      .map((s) => s.task_description || "untitled")
      .join(", ");
    return `Add ${traces.length} traces: ${descs}`;
  });

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleAdd = () => {
    if (selected.size === 0) return;
    add.mutate(
      { traceIds: Array.from(selected), message },
      { onSuccess: () => onClose() },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />

      {/* Dialog */}
      <div className="relative bg-[var(--surface)] border border-[var(--border)] w-full max-w-lg mx-4">
        <div className="px-4 py-3 border-b border-[var(--border)]">
          <span className="text-[12px] uppercase tracking-wider font-[family-name:var(--font-mono)] text-[var(--text)]">
            add traces
          </span>
        </div>

        {/* Trace list */}
        <div className="px-4 py-2 max-h-[200px] overflow-y-auto">
          {traces.map((s) => (
            <label
              key={s.trace_id}
              className="flex items-center gap-2 py-1 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={selected.has(s.trace_id)}
                onChange={() => toggle(s.trace_id)}
                className="accent-[var(--green)]"
              />
              <span className="text-[11px] font-[family-name:var(--font-mono)] text-[var(--text)] truncate">
                {s.task_description || s.trace_id}
              </span>
              <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-dim)] ml-auto flex-none">
                {s.step_count}s
              </span>
            </label>
          ))}
        </div>

        {/* Message */}
        <div className="px-4 py-2 border-t border-[var(--border)]">
          <label className="block text-[9px] uppercase tracking-wider font-[family-name:var(--font-mono)] text-[var(--text-muted)] mb-1">
            message
          </label>
          <input
            type="text"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            className="w-full bg-[var(--bg-alt)] border border-[var(--border)] px-2 py-1 text-[11px] font-[family-name:var(--font-mono)] text-[var(--text)] outline-none focus:border-[var(--accent)]"
          />
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-[var(--border)]">
          <button
            onClick={onClose}
            className="text-[11px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] hover:text-[var(--text)] px-2 py-0.5 border border-[var(--border)] cursor-pointer transition-colors duration-100"
          >
            [cancel]
          </button>
          <button
            onClick={handleAdd}
            disabled={selected.size === 0 || add.isPending}
            className="text-[11px] font-[family-name:var(--font-mono)] text-[var(--green)] px-2 py-0.5 border border-[var(--green)] cursor-pointer transition-colors duration-100 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            [add {selected.size}]
          </button>
        </div>
      </div>
    </div>
  );
}
