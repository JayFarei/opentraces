import type { Theme } from "../tokens";
import { F } from "../tokens";

export type View = "review" | "graph";

export function NavBar({
  t, view, setView, mode, setMode,
}: {
  t: Theme; view: View; setView: (v: View) => void;
  mode: "dark" | "light"; setMode: (m: "dark" | "light") => void;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "0 20px", borderBottom: `1px solid ${t.border}`,
      background: t.panelBg,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <span style={{ fontFamily: F.code, fontSize: 14, fontWeight: 400, color: t.textSec, padding: "10px 0" }}>
          open<span style={{ color: t.text, fontWeight: 700 }}>traces</span>
        </span>
        <div style={{ display: "flex" }}>
          {(["review", "graph"] as const).map((v) => (
            <span
              key={v}
              onClick={() => setView(v)}
              style={{
                fontFamily: F.code, fontSize: 12, padding: "10px 14px",
                color: view === v ? t.text : t.textMuted,
                borderBottom: view === v ? `2px solid ${t.accent}` : "2px solid transparent",
                fontWeight: view === v ? 500 : 400,
                cursor: "pointer",
              }}
            >
              {v}
            </span>
          ))}
        </div>
      </div>
      <div
        onClick={() => setMode(mode === "dark" ? "light" : "dark")}
        style={{
          fontFamily: F.code, fontSize: 11, padding: "4px 12px",
          border: `1px solid ${t.border}`, color: t.textSec,
          cursor: "pointer", userSelect: "none",
        }}
      >
        {mode}
      </div>
    </div>
  );
}
