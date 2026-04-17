import type { CSSProperties, ReactNode } from "react";
import type { Theme } from "../tokens";
import { F } from "../tokens";

export function Panel({
  n, label, t, children, style, bodyStyle,
}: {
  n: number; label: string; t: Theme; children: ReactNode;
  style?: CSSProperties; bodyStyle?: CSSProperties;
}) {
  return (
    <div style={{
      border: `1px solid ${t.border}`, position: "relative",
      display: "flex", flexDirection: "column",
      background: t.panelBg,
      minHeight: 0,
      minWidth: 0,
      overflow: "visible",
      ...style,
    }}>
      <div style={{
        position: "absolute", top: -1, left: 12, transform: "translateY(-50%)",
        background: t.panelBg,
        border: `1px solid ${t.border}`,
        borderRadius: 20,
        padding: "3px 12px",
        fontFamily: F.label, fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase",
        display: "flex", alignItems: "center", gap: 5, whiteSpace: "nowrap",
        zIndex: 1,
      }}>
        <span style={{ color: t.textDim }}>{n}</span>
        <span style={{ color: t.textSec }}>{label}</span>
      </div>
      <div style={{ padding: "12px 0 0", flex: 1, display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden", ...bodyStyle }}>
        {children}
      </div>
    </div>
  );
}

export function Label({
  children, t, style,
}: {
  children: ReactNode; t: Theme; style?: CSSProperties;
}) {
  return <div style={{
    fontFamily: F.label, fontSize: 10, letterSpacing: "0.1em",
    textTransform: "uppercase", color: t.textMuted, ...style,
  }}>{children}</div>;
}
