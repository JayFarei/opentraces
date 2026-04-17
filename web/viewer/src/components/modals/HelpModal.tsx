import { useEffect } from "react";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { Label } from "../Panel";

const SHORTCUTS: Array<[string, string]> = [
  ["j / k", "Move inbox selection"],
  ["space", "Add or remove the selected trace from staged"],
  ["r / ↻", "Refresh inbox from the local session files"],
  ["?", "Toggle this help"],
  ["q", "Quit the local web session"],
];

export function HelpModal({
  t, onClose,
}: { t: Theme; onClose: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
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
          maxWidth: 620,
          width: "92%",
          fontFamily: F.code,
          fontSize: 12,
        }}
      >
        <div style={{ color: t.text, fontWeight: 700, fontSize: 15, marginBottom: 16 }}>
          Review help
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 18, color: t.textSec, lineHeight: 1.7 }}>
          <div>
            <Label t={t} style={{ marginBottom: 6 }}>Keybindings</Label>
            <div style={{ display: "grid", gridTemplateColumns: "96px 1fr", gap: "6px 12px" }}>
              {SHORTCUTS.map(([key, description]) => (
                <div key={key} style={{ display: "contents" }}>
                  <div style={{ color: t.cyan }}>{key}</div>
                  <div>{description}</div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <Label t={t} style={{ marginBottom: 6 }}>Trace row legend</Label>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div><span style={{ color: t.textDim }}>·</span> Normal trace</div>
              <div><span style={{ color: t.cyan, opacity: 0.75 }}>◐</span> Recently touched in roughly the last 2 hours</div>
              <div><span style={{ color: t.yellow }}>●</span> Security findings still need review</div>
              <div><span style={{ color: t.red }}>●</span> Blocked trace</div>
              <div>
                <span style={{ color: t.cyan, opacity: 0.75 }}>↑N</span>{" "}
                Session generation. <span style={{ color: t.cyan }}>↑1</span> is the first captured trace
                for that session; <span style={{ color: t.cyan }}>↑2+</span> means the same session kept
                going and this newer trace replaces an older one. Refresh pulls the latest trace, and
                you should review and push the latest generation.
              </div>
            </div>
          </div>
        </div>

        <div style={{ color: t.textDim, fontSize: 11, marginTop: 20 }}>Esc or ? to close</div>
      </div>
    </div>
  );
}
