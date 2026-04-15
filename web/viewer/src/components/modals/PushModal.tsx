import { useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { api } from "../../lib/api";

export function PushModal({
  t, onClose, remote,
}: { t: Theme; onClose: () => void; remote: string | null }) {
  const qc = useQueryClient();
  const push = useMutation({
    mutationFn: () => api.push(),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["traces"] });
      onClose();
    },
  });

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
      if (e.key === "L" || e.key === "l") { e.preventDefault(); push.mutate(); }
      if (e.key === "I" || e.key === "i") { e.preventDefault(); push.mutate(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose, push]);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.55)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: t.panelBg,
          border: `1px solid ${t.cyan}40`,
          boxShadow: `0 0 40px ${t.cyan}10, 0 8px 32px rgba(0,0,0,0.4)`,
          padding: "28px 36px",
          maxWidth: 480, width: "90%",
          fontFamily: F.code, fontSize: 13,
        }}
      >
        <div style={{ color: t.text, fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
          Push staged traces
        </div>
        <div style={{ color: t.cyan, fontSize: 12, marginBottom: 20 }}>
          remote&nbsp;&nbsp;<span style={{ color: t.textSec }}>{remote || "not set"}</span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 20 }}>
          <div
            onClick={() => push.mutate()}
            style={{
              display: "flex", alignItems: "baseline", gap: 12, padding: "8px 12px",
              border: `1px solid ${t.border}`, cursor: "pointer", background: "transparent",
            }}
          >
            <span style={{ color: t.text, fontWeight: 700, minWidth: 16 }}>L</span>
            <span style={{ color: t.text }}>LLM review then push</span>
            <span style={{ color: t.textDim, fontSize: 11, marginLeft: "auto" }}>opentraces push --llm-review</span>
          </div>
          <div
            onClick={() => push.mutate()}
            style={{
              display: "flex", alignItems: "baseline", gap: 12, padding: "8px 12px",
              border: `1px solid ${t.border}`, cursor: "pointer", background: "transparent",
            }}
          >
            <span style={{ color: t.text, fontWeight: 700, minWidth: 16 }}>I</span>
            <span style={{ color: t.text }}>Ignore and push</span>
            <span style={{ color: t.textDim, fontSize: 11, marginLeft: "auto" }}>opentraces push</span>
          </div>
        </div>

        {push.isError && (
          <div style={{ color: t.red, fontSize: 11, marginBottom: 12 }}>
            {(push.error as Error).message}
          </div>
        )}

        <div style={{ color: t.textDim, fontSize: 11 }}>
          {push.isPending ? "Pushing…" : "Esc to cancel"}
        </div>
      </div>
    </div>
  );
}
