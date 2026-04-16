"use client";

import { useState } from "react";
import SectionRule from "./SectionRule";

type Trace = { short: string; lines: number[]; colorVar: string };

const TRACES: Record<string, Trace> = {
  "f706491a": {
    short: "f706491a",
    lines: [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20],
    colorVar: "var(--cyan)",
  },
  "66717c54": {
    short: "66717c54",
    lines: [5, 15],
    colorVar: "var(--accent)",
  },
};

const TOTAL = 20;
const FILE = "config/server.py";
const LINE_WIDTHS = [68, 42, 85, 58, 34, 92, 66, 50, 78, 62, 40, 75, 53, 88, 32, 64, 80, 46, 72, 56];

function getTrace(line: number): ({ id: string } & Trace) | null {
  for (const [id, t] of Object.entries(TRACES)) {
    if (t.lines.includes(line)) return { id, ...t };
  }
  return null;
}

function mix(color: string, pct: number): string {
  return `color-mix(in oklab, ${color} ${pct}%, transparent)`;
}

// ── semantic diff pills (web/viewer convention) ──
type DiffSym = "+" | "~" | "-" | "↷";
type Count = { sym: DiffSym; n: number };

const DIFF_COLOR: Record<DiffSym, string> = {
  "+": "var(--green)",
  "~": "var(--cyan)",
  "-": "var(--red)",
  "↷": "var(--yellow)",
};

function DiffPill({ sym, n }: Count) {
  const color = DIFF_COLOR[sym];
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 2,
      padding: "0 5px",
      border: `1px solid ${mix(color, 40)}`,
      background: mix(color, 10),
      color,
      fontFamily: "var(--font-mono)", fontSize: 10, lineHeight: 1.5,
      whiteSpace: "nowrap",
    }}>
      <span style={{ fontWeight: 700 }}>{sym}</span>
      <span>{n}</span>
    </span>
  );
}

function CovPill({ value }: { value: string }) {
  const color = value === "100%" ? "var(--green)" : "var(--accent)";
  return (
    <span style={{
      padding: "0 6px",
      border: `1px solid ${mix(color, 40)}`,
      background: mix(color, 10),
      color,
      fontFamily: "var(--font-mono)", fontSize: 10, lineHeight: 1.5,
      fontWeight: 600, whiteSpace: "nowrap",
    }}>{value}</span>
  );
}

// ── graph data (trimmed) ──
type GLine =
  | { k: "trunk"; t: string }
  | { k: "commit"; tree: string; hash: string; msg: string; cov?: string }
  | { k: "sess"; tree: string; hash: string; counts?: Count[]; noun?: string; detail?: string };

const cmt = (tree: string, hash: string, msg: string, cov?: string): GLine => ({ k: "commit", tree, hash, msg, cov });
const ses = (tree: string, hash: string, counts: Count[] | undefined, noun?: string, detail?: string): GLine => ({ k: "sess", tree, hash, counts, noun, detail });
const tr = (t: string): GLine => ({ k: "trunk", t });

const GRAPH: GLine[] = [
  cmt("┊●   ", "7c3b1927", "marketing skill"),
  tr("┊"),
  ses("┊╭┄", "5baac494", [{ sym: "~", n: 1 }], undefined, "push"),
  cmt("┊●   ", "6899f64c", "fix(publish): report failed loads", "100%"),
  tr("├╯"),
  tr("┊"),
  ses("┊╭┄", "fa2fe980", [{ sym: "+", n: 14 }, { sym: "~", n: 4 }, { sym: "-", n: 1 }, { sym: "↷", n: 1 }], "fns"),
  ses("┊├┄", "be59b9f8", [{ sym: "+", n: 7 }, { sym: "~", n: 10 }], "fns"),
  ses("┊├┄", "0e7c776c", [{ sym: "+", n: 10 }, { sym: "~", n: 5 }], "fns"),
  cmt("┊●   ", "83d26672", "feat(capture): resume sessions", "100%"),
  tr("├╯"),
  tr("┊"),
  ses("┊╭┄", "6606fc1f", [{ sym: "+", n: 8 }, { sym: "~", n: 12 }, { sym: "-", n: 2 }], "fns"),
  ses("┊├┄", "fe8fe3fd", [{ sym: "+", n: 6 }, { sym: "~", n: 1 }], "fns"),
  cmt("┊●   ", "ac019172", "feat(viewer): hover actions", "100%"),
  tr("├╯"),
  tr("┊"),
  ses("┊╭┄", "6606fc1f", [{ sym: "~", n: 2 }]),
  cmt("┊●   ", "92c23ccc", "Refine trace map", "35%"),
  tr("├╯"),
];

// ── component ──
export default function Attribution() {
  const [hTrace, setHTrace] = useState<string | null>(null);
  const [hLine, setHLine] = useState<number | null>(null);
  const codeLines = Array.from({ length: TOTAL }, (_, i) => ({ n: i + 1, t: getTrace(i + 1) }));

  return (
    <section>
      <SectionRule label="attribution" />
      <div className="section-title">Who wrote this line of code?</div>
      <p className="section-sub" style={{ maxWidth: 560 }}>
        When agents write the code, <code style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>git blame</code> points at a session, not a person.{" "}
        <code style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>ot blame</code> and{" "}
        <code style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>otd graph</code> resolve every line and commit back to the trace that produced it.
      </p>

      <div className="attr-panels" style={{ border: "1px solid var(--border)", display: "flex", background: "var(--bg)" }}>
        <div className="attr-pane attr-pane-blame" style={{ flex: 1, minWidth: 0, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column" }}>
          <PaneHeader label="blame" hint={FILE} />
          <BlameView codeLines={codeLines} hTrace={hTrace} setHTrace={setHTrace} hLine={hLine} setHLine={setHLine} />
        </div>
        <div className="attr-pane attr-pane-graph" style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <PaneHeader label="graph" hint="--limit 5" />
          <GraphView />
        </div>
      </div>
    </section>
  );
}

function PaneHeader({ label, hint }: { label: string; hint: string }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "8px 14px", borderBottom: "1px solid var(--border)",
      fontFamily: "var(--font-mono)", fontSize: 12,
    }}>
      <span style={{ color: "var(--text)", fontWeight: 500 }}>{label}</span>
      <span style={{ color: "var(--text-dim)", fontSize: 11 }}>{hint}</span>
    </div>
  );
}

// ── blame (file view only, no sessions sidebar) ──
function BlameView({
  codeLines,
  hTrace,
  setHTrace,
  hLine,
  setHLine,
}: {
  codeLines: { n: number; t: ({ id: string } & Trace) | null }[];
  hTrace: string | null;
  setHTrace: (v: string | null) => void;
  hLine: number | null;
  setHLine: (v: number | null) => void;
}) {
  return (
    <div style={{ background: "var(--bg-alt)", flex: 1, display: "flex", padding: "8px 0", minWidth: 0 }}>
      {/* color stripe */}
      <div style={{ display: "flex", flexDirection: "column" }}>
        {codeLines.map(({ n, t: trace }) => {
          if (!trace) return <div key={n} style={{ width: 3, height: 22 }} />;
          const hi = hTrace && trace.id === hTrace;
          const dim = hTrace && trace.id !== hTrace;
          const bg = dim ? mix(trace.colorVar, 10) : hi ? trace.colorVar : mix(trace.colorVar, 55);
          return (
            <div key={n}
              onMouseEnter={() => { setHLine(n); setHTrace(trace.id); }}
              onMouseLeave={() => { setHLine(null); setHTrace(null); }}
              style={{ width: 3, height: 22, background: bg, transition: "all 0.1s", cursor: "pointer" }}
            />
          );
        })}
      </div>

      {/* session id (shown on first line of each run) */}
      <div style={{ display: "flex", flexDirection: "column", padding: "0 10px" }}>
        {codeLines.map(({ n, t: trace }) => {
          if (!trace) return <div key={n} style={{ height: 22, minWidth: 64 }} />;
          const prev = codeLines[n - 2];
          const isNew = !prev || prev.t?.id !== trace.id;
          const dim = hTrace && trace.id !== hTrace;
          return (
            <div key={n}
              onMouseEnter={() => { setHLine(n); setHTrace(trace.id); }}
              onMouseLeave={() => { setHLine(null); setHTrace(null); }}
              style={{
                height: 22, display: "flex", alignItems: "center",
                fontFamily: "var(--font-mono)", fontSize: 10,
                color: dim ? "var(--text-dim)" : mix(trace.colorVar, 75),
                minWidth: 64, cursor: "pointer", transition: "color 0.1s",
              }}
            >{isNew ? trace.short : ""}</div>
          );
        })}
      </div>

      {/* line numbers */}
      <div style={{ display: "flex", flexDirection: "column", padding: "0 10px", userSelect: "none" }}>
        {codeLines.map(({ n, t: trace }) => {
          const dim = hTrace && trace && trace.id !== hTrace;
          return (
            <div key={n}
              onMouseEnter={() => { setHLine(n); if (trace) setHTrace(trace.id); }}
              onMouseLeave={() => { setHLine(null); setHTrace(null); }}
              style={{
                height: 22, display: "flex", alignItems: "center", justifyContent: "flex-end", minWidth: 20,
                fontFamily: "var(--font-mono)", fontSize: 11,
                color: dim ? "var(--text-dim)" : "var(--text-muted)",
                transition: "color 0.1s", cursor: "pointer",
              }}
            >{n}</div>
          );
        })}
      </div>

      {/* code skeleton */}
      <div style={{ display: "flex", flexDirection: "column", flex: 1, padding: "0 16px", minWidth: 0 }}>
        {codeLines.map(({ n, t: trace }) => {
          if (!trace) return <div key={n} style={{ height: 22 }} />;
          const dim = hTrace && trace.id !== hTrace;
          const hi = hTrace && trace.id === hTrace;
          const lineHov = hLine === n;
          const w = LINE_WIDTHS[(n - 1) % LINE_WIDTHS.length];
          const indent = [5, 15].includes(n) ? 24 : [6, 7, 8, 9, 10, 11, 12, 13, 14].includes(n) ? 16 : 0;
          return (
            <div key={n}
              onMouseEnter={() => { setHLine(n); setHTrace(trace.id); }}
              onMouseLeave={() => { setHLine(null); setHTrace(null); }}
              style={{
                height: 22, display: "flex", alignItems: "center",
                paddingLeft: indent, cursor: "pointer",
                background: lineHov ? "var(--surface-hover)" : "transparent",
                transition: "background 0.1s",
              }}
            >
              <div style={{
                height: 4, width: `${w}%`, maxWidth: 260,
                background: dim ? mix(trace.colorVar, 6) : hi ? mix(trace.colorVar, 40) : mix(trace.colorVar, 18),
                transition: "all 0.1s",
              }} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── graph ──
function GraphView() {
  return (
    <div style={{ background: "var(--bg-alt)", flex: 1, padding: "10px 14px", overflow: "auto", minWidth: 0 }}>
      {GRAPH.map((line, i) => {
        if (line.k === "trunk") {
          return (
            <div key={i} style={{ fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.8, color: "var(--text-dim)", whiteSpace: "pre" }}>
              {line.t}
            </div>
          );
        }
        if (line.k === "commit") {
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.8, minHeight: 20, whiteSpace: "nowrap", minWidth: 0 }}>
              <span style={{ color: "var(--text-dim)", whiteSpace: "pre" }}>{line.tree}</span>
              <span style={{ color: "var(--accent)" }}>c:</span>
              <span style={{ color: "var(--text)" }}>{line.hash}</span>
              <span style={{ color: "var(--text-secondary)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", flex: "0 1 auto" }}>{line.msg}</span>
              {line.cov && <CovPill value={line.cov} />}
            </div>
          );
        }
        // sess
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.8, minHeight: 20, whiteSpace: "nowrap", minWidth: 0 }}>
            <span style={{ color: "var(--text-dim)", whiteSpace: "pre" }}>{line.tree}</span>
            <span style={{ color: "var(--accent)" }}>s:</span>
            <span style={{ color: "var(--text-muted)" }}>{line.hash}</span>
            {line.counts && (
              <span style={{ display: "inline-flex", gap: 3 }}>
                {line.counts.map((c, j) => <DiffPill key={j} sym={c.sym} n={c.n} />)}
              </span>
            )}
            {line.noun && <span style={{ color: "var(--text-muted)" }}>{line.noun}</span>}
            {line.detail && (
              <>
                <span style={{ color: "var(--text-dim)" }}>│</span>
                <span style={{ color: "var(--text-muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", flex: "0 1 auto" }}>{line.detail}</span>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
