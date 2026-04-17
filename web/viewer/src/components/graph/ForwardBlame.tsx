import type { Theme } from "../../tokens";
import { F, pctColor, traceColor } from "../../tokens";
import type { BlamePayload } from "../../lib/api";
import { SemanticDiffSummary, parseSemanticDiffSummary } from "./SemanticDiffSummary";

function ClaudeLogo({ size = 14, color }: { size?: number; color: string }) {
  const n = 13, cx = 16, cy = 16;
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      {Array.from({ length: n }, (_, i) => {
        const a = (i * 360 / n - 90) * Math.PI / 180;
        const o = 12 + (i % 3 === 0 ? 1.5 : i % 2 === 0 ? 0.5 : 0);
        const pa = a + Math.PI / 2;
        const p1x = cx + Math.cos(a) * 3.5 + Math.cos(pa) * 1.4;
        const p1y = cy + Math.sin(a) * 3.5 + Math.sin(pa) * 1.4;
        const p2x = cx + Math.cos(a) * o + Math.cos(pa) * 0.8;
        const p2y = cy + Math.sin(a) * o + Math.sin(pa) * 0.8;
        const p3x = cx + Math.cos(a) * o - Math.cos(pa) * 0.8;
        const p3y = cy + Math.sin(a) * o - Math.sin(pa) * 0.8;
        const p4x = cx + Math.cos(a) * 3.5 - Math.cos(pa) * 1.4;
        const p4y = cy + Math.sin(a) * 3.5 - Math.sin(pa) * 1.4;
        return <polygon key={i} fill={color} points={`${p1x},${p1y} ${p2x},${p2y} ${p3x},${p3y} ${p4x},${p4y}`} />;
      })}
    </svg>
  );
}

export function ForwardBlame({
  t, blame, mode, onSelectTrace, commitMsgFallback,
}: {
  t: Theme; blame: BlamePayload; mode: "dark" | "light";
  onSelectTrace: (traceId: string) => void;
  commitMsgFallback?: string;
}) {
  const commitMsg = blame.msg?.trim() || commitMsgFallback || "(no subject)";
  const pctNum = typeof blame.pct === "number" ? blame.pct : 0;

  return (
    <div style={{ fontFamily: F.code, fontSize: 12, lineHeight: 1.7 }}>
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <span style={{ color: pctColor(pctNum, t) }}>●</span>
          <span style={{ color: t.textMuted }}>Commit:</span>
          <span style={{
            color: t.yellow, fontWeight: 600, textDecoration: "underline",
            textDecorationColor: `${t.yellow}40`, textUnderlineOffset: 2,
          }}>{blame.id}</span>
          <span data-testid="graph-blame-commit-msg" style={{ color: t.text, fontWeight: 500 }}>{commitMsg}</span>
        </div>
        <div style={{ color: t.textMuted, fontSize: 11, paddingLeft: 18, marginTop: 2 }}>
          Coverage: <span style={{ color: pctColor(pctNum, t) }}>{blame.coverage}</span>{" "}
          attributed ({blame.attributed})&nbsp;&nbsp;
          {blame.sessions.length} trace{blame.sessions.length === 1 ? "" : "s"}&nbsp;&nbsp;
          {blame.fileCount} files
        </div>
      </div>

      {blame.sessions.map((s, i) => {
        const dc = traceColor(s.trace_id, mode);
        const entityParts = parseSemanticDiffSummary(s.entities);
        return (
          <div
            key={s.trace_id + i}
            onClick={() => s.canonical && onSelectTrace(s.trace_id)}
            style={{
              marginBottom: 14, paddingLeft: 8,
              cursor: s.canonical ? "pointer" : "default",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
              <div style={{
                width: 18, height: 18, minWidth: 18, borderRadius: "50%",
                background: `${dc}15`, border: `1px solid ${dc}35`,
                display: "flex", alignItems: "center", justifyContent: "center",
              }}>
                <ClaudeLogo size={9} color={`${dc}CC`} />
              </div>
              <span style={{ color: t.textDim }}>{s.canonical ? "t:" : "s:"}</span>
              <span style={{
                color: t.yellow, textDecoration: "underline",
                textDecorationColor: `${t.yellow}40`, textUnderlineOffset: 2,
              }}>{s.id}</span>
              {s.name && <span style={{ color: t.green, fontWeight: 600 }}>{s.name}</span>}
              <span style={{ color: t.textSec }}>{s.lines} lines</span>
              <span style={{ color: t.textDim }}>.</span>
              <span style={{ color: (parseInt(s.pct, 10) || 0) >= 20 ? t.green : t.textMuted }}>{s.pct}</span>
              {s.model && <span style={{ color: t.cyan, fontSize: 11 }}>{s.model}</span>}
            </div>
            {s.entities && (
              <div style={{ fontSize: 11, paddingLeft: 26, marginTop: 4, minWidth: 0 }}>
                <SemanticDiffSummary
                  text={s.entities}
                  t={t}
                  compact
                  stacked={entityParts.length > 1}
                />
              </div>
            )}
          </div>
        );
      })}

      {blame.hookLinked && blame.hookLinked.length > 0 && (
        <>
          <div style={{ borderTop: `1px solid ${t.border}`, margin: "16px 0" }} />
          <div style={{ marginBottom: 8, color: t.text, fontWeight: 500 }}>
            Hook-linked traces{" "}
            <span style={{ color: t.textMuted, fontWeight: 400, fontSize: 11 }}>
              (tool-emit evidence; no per-line attribution)
            </span>
          </div>
          <div style={{ fontSize: 11 }}>
            {blame.hookLinked.map((h) => {
              const dc = traceColor(h.trace_id, mode);
              return (
                <div
                  key={h.trace_id}
                  onClick={() => onSelectTrace(h.trace_id)}
                  style={{
                    display: "flex", gap: 8, padding: "4px 0",
                    alignItems: "baseline", cursor: "pointer", paddingLeft: 8,
                  }}
                >
                  <div style={{
                    width: 14, height: 14, minWidth: 14, borderRadius: "50%",
                    background: `${dc}15`, border: `1px solid ${dc}35`,
                  }} />
                  <span style={{ color: t.textDim }}>t:</span>
                  <span style={{
                    color: t.yellow, textDecoration: "underline",
                    textDecorationColor: `${t.yellow}40`, textUnderlineOffset: 2,
                  }}>{h.id}</span>
                  {h.name && <span style={{ color: t.green, fontWeight: 600 }}>{h.name}</span>}
                  {h.model && <span style={{ color: t.cyan, fontSize: 10 }}>{h.model}</span>}
                </div>
              );
            })}
          </div>
        </>
      )}

      {blame.files.length > 0 && (
        <>
          <div style={{ borderTop: `1px solid ${t.border}`, margin: "16px 0" }} />
          <div style={{ marginBottom: 8, color: t.text, fontWeight: 500 }}>Files:</div>
          <div style={{ fontSize: 11 }}>
            {blame.files.map((f, i) => (
              <div key={i} style={{ display: "flex", gap: 12, padding: "3px 0", alignItems: "baseline" }}>
                <span style={{
                  color: t.textSec, flex: 1, minWidth: 0,
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                }}>{f.path}</span>
                <span style={{ color: t.cyan, minWidth: 70, textAlign: "right", whiteSpace: "nowrap" }}>
                  {f.total} line(s)
                </span>
                <span style={{ color: t.textMuted, fontSize: 10, whiteSpace: "nowrap" }}>
                  ({f.attr} attributed)
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export { ClaudeLogo };
