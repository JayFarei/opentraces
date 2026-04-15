import { useEffect } from "react";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { Label } from "../Panel";

export function SecurityInfoModal({
  t, onClose, remote,
}: { t: Theme; onClose: () => void; remote: string | null }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

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
          maxWidth: 520, width: "90%",
          fontFamily: F.code, fontSize: 12,
        }}
      >
        <div style={{ color: t.text, fontWeight: 700, fontSize: 15, marginBottom: 16 }}>
          Security info
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, color: t.textSec, lineHeight: 1.7 }}>
          <div>
            <Label t={t} style={{ marginBottom: 4 }}>TIER 1 · REGEX + ENTROPY</Label>
            <div><span style={{ color: t.green }}>●</span> Always on — secret patterns, high-entropy blobs</div>
          </div>
          <div>
            <Label t={t} style={{ marginBottom: 4 }}>TIER 1.5 · TRUFFLEHOG</Label>
            <div><span style={{ color: t.textMuted }}>●</span> Opt-in — <span style={{ color: t.cyan }}>opentraces setup trufflehog</span></div>
          </div>
          <div>
            <Label t={t} style={{ marginBottom: 4 }}>TIER 2 · LLM REVIEW</Label>
            <div><span style={{ color: t.textMuted }}>●</span> Opt-in — <span style={{ color: t.cyan }}>opentraces llm-review</span></div>
          </div>
          <div>
            <Label t={t} style={{ marginBottom: 4 }}>PUSH TARGET</Label>
            <div>Remote: <span style={{ color: t.cyan }}>{remote || "not set"}</span></div>
          </div>
        </div>
        <div style={{ color: t.textDim, fontSize: 11, marginTop: 20 }}>Esc to close</div>
      </div>
    </div>
  );
}
