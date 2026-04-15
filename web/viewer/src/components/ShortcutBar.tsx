import type { Theme } from "../tokens";
import { F } from "../tokens";

export function ShortcutBar({ t, items }: { t: Theme; items: [string, string][] }) {
  return (
    <div style={{
      padding: "8px 16px", borderTop: `1px solid ${t.border}`,
      display: "flex", gap: 6, fontFamily: F.code, fontSize: 11,
      flexWrap: "wrap", background: t.panelBg,
    }}>
      {items.map(([k, l], i) => (
        <span key={i} style={{ marginRight: 8 }}>
          <span style={{ color: t.cyan, fontWeight: 500 }}>{k}</span>{" "}
          <span style={{ color: t.textMuted }}>{l}</span>
        </span>
      ))}
    </div>
  );
}
