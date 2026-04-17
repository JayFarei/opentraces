export type Theme = typeof DARK;

export const DARK = {
  bg: "#111111", bgAlt: "#0A0A0A", surface: "#191919",
  surfaceHover: "#222222", surfaceElevated: "#1E1E1E",
  text: "#E0E0E0", textSec: "#B0B0B0", textMuted: "#666666", textDim: "#444444",
  accent: "#F97316", accentBg: "rgba(249,115,22,0.08)",
  cyan: "#22D3EE", green: "#22C55E", blue: "#3B82F6",
  yellow: "#EAB308", red: "#EF4444",
  border: "#2A2A2A", borderStrong: "#3A3A3A",
  pageBg: "#1A1A1A", panelBg: "#0F0F0F",
};

export const LIGHT: Theme = {
  bg: "#E4E2DF", bgAlt: "#D8D6D2", surface: "#EEEDEB",
  surfaceHover: "#E4E2DF", surfaceElevated: "#F2F1EF",
  text: "#0A0A0A", textSec: "#2A2A2A", textMuted: "#6B6B6B", textDim: "#A0A0A0",
  accent: "#C2410C", accentBg: "rgba(194,65,12,0.08)",
  cyan: "#0E7490", green: "#15803D", blue: "#1D4ED8",
  yellow: "#92400E", red: "#B91C1C",
  border: "#C5C3BF", borderStrong: "#9A9895",
  pageBg: "#D8D6D2", panelBg: "#EEEDEB",
};

export const F = {
  display: "'Space Grotesk', sans-serif",
  body: "'IBM Plex Mono', monospace",
  code: "'JetBrains Mono', monospace",
  label: "'Space Mono', monospace",
};

export function pctColor(p: number, t: Theme): string {
  return p >= 70 ? t.green : p >= 30 ? t.yellow : t.red;
}

export function trunc(s: string | undefined | null, n: number): string {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// Trace colour palettes (by full trace_id — hash-backed fallback keeps
// per-commit trace dots stable even when the id isn't in the curated list).
const DARK_PALETTE = ["#F97316", "#22D3EE", "#3B82F6", "#EAB308", "#22C55E", "#A78BFA", "#F43F5E"];
const LIGHT_PALETTE = ["#C2410C", "#0E7490", "#1D4ED8", "#92400E", "#15803D", "#6D28D9", "#BE123C"];

function hashId(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export function traceColor(id: string, mode: "dark" | "light"): string {
  const palette = mode === "dark" ? DARK_PALETTE : LIGHT_PALETTE;
  return palette[hashId(id) % palette.length]!;
}
