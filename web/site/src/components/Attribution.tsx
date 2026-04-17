import { Fragment, ReactNode } from "react";
import SectionRule from "./SectionRule";

// ── Graph data ─────────────────────────────────────────────
type GCount = { add?: number; mod?: number; del?: number; ren?: number };
type GLine =
  | { kind: "trunk"; text: string }
  | { kind: "commit"; tree: string; hash: string; msg: string; cov?: string }
  | { kind: "sess"; tree: string; hash: string; counts?: GCount; noun?: string; detail?: string };

const GRAPH: GLine[] = [
  { kind: "sess", tree: "┊╭┄", hash: "5baac494", counts: { mod: 1 }, detail: "push" },
  { kind: "commit", tree: "┊●   ", hash: "6899f64c", msg: "fix(publish): report failed loads", cov: "100%" },
  { kind: "trunk", text: "├╯" },
  { kind: "trunk", text: "┊" },
  { kind: "sess", tree: "┊╭┄", hash: "fa2fe980", counts: { add: 14, mod: 4, del: 1, ren: 1 }, noun: "fns" },
  { kind: "sess", tree: "┊├┄", hash: "be59b9f8", counts: { add: 7, mod: 10 }, noun: "fns", detail: "state, test_resume" },
  { kind: "sess", tree: "┊├┄", hash: "0e7c776c", counts: { add: 10, mod: 5 }, noun: "fns", detail: "resume, parse" },
  { kind: "commit", tree: "┊●   ", hash: "83d26672", msg: "feat(capture): resume sessions", cov: "100%" },
  { kind: "trunk", text: "├╯" },
  { kind: "trunk", text: "┊" },
  { kind: "sess", tree: "┊╭┄", hash: "6606fc1f", counts: { add: 8, mod: 12, del: 2 }, noun: "fns", detail: "server, graph_api" },
  { kind: "sess", tree: "┊├┄", hash: "fe8fe3fd", counts: { add: 6, mod: 1 }, noun: "fns", detail: "PushModal" },
  { kind: "commit", tree: "┊●   ", hash: "ac019172", msg: "feat(viewer): hover actions", cov: "100%" },
  { kind: "trunk", text: "├╯" },
  { kind: "trunk", text: "┊" },
  { kind: "sess", tree: "┊╭┄", hash: "6606fc1f", counts: { mod: 2 } },
  { kind: "commit", tree: "┊●   ", hash: "92c23ccc", msg: "Refine trace map", cov: "35%" },
  { kind: "trunk", text: "├╯" },
];

// ── Blame data — sums to 100% of the commit's diff ─────────
const COMMIT = {
  hash: "ac019172",
  msg: "feat(viewer): hover actions, two-stage push",
  diffLines: 94,
  filesCount: 5,
};

type TraceRow = {
  hash: string;
  model: string;
  lines: number;
  pct: number;
  added?: string[];
  modified?: string[];
};

const TRACES: TraceRow[] = [
  {
    hash: "6606fc1f",
    model: "claude-code",
    lines: 45,
    pct: 48,
    added: ["server", "graph_api", "api"],
    modified: ["__file_patterns__", "PushSelected"],
  },
  {
    hash: "fe8fe3fd",
    model: "claude-code",
    lines: 30,
    pct: 32,
    added: ["PushModal", "PushModal.onOpen"],
  },
  {
    hash: "5baac494",
    model: "claude-code",
    lines: 19,
    pct: 20,
    added: ["ReviewView", "TraceSecurityModal"],
  },
];

const FILES = [
  { path: "components/review/PushModal.tsx", lines: 45, edits: 12 },
  { path: "components/modals/TraceSecurityModal.tsx", lines: 21, edits: 6 },
  { path: "lib/conversation.ts", lines: 15, edits: 4 },
  { path: "App.tsx", lines: 8, edits: 2 },
  { path: "components/review/ReviewView.tsx", lines: 5, edits: 1 },
];

// ── helpers ────────────────────────────────────────────────
function Counts({ c }: { c: GCount }) {
  const items: ReactNode[] = [];
  if (c.add) items.push(<span key="a" className="ok">+{c.add}</span>);
  if (c.mod) items.push(<span key="m" className="n">~{c.mod}</span>);
  if (c.del) items.push(<span key="d" className="er">-{c.del}</span>);
  if (c.ren) items.push(<span key="r" className="w">↷{c.ren}</span>);
  return (
    <>
      {items.map((node, i) => (
        <span key={i}>
          {i > 0 && " "}
          {node}
        </span>
      ))}
    </>
  );
}

// ── component ──────────────────────────────────────────────
export default function Attribution() {
  return (
    <section>
      <SectionRule label="attribution" />
      <div className="section-title">Who wrote this line of code?</div>
      <p className="section-sub" style={{ maxWidth: 620, marginBottom: 16 }}>
        When agents write most of the code, <strong style={{ color: "var(--text)" }}>git blame</strong> points at a user, not a session id.{" "}
        <strong style={{ color: "var(--text)" }}>ot blame</strong> and <strong style={{ color: "var(--text)" }}>ot graph</strong> resolve every commit back to the traces that produced it — so you can see what worked and what didn't across your agent sessions.
      </p>
      <p className="section-sub" style={{ maxWidth: 620 }}>
        <strong style={{ color: "var(--text)" }}>Attribution</strong> search can surface as a semantic diff — added functions, modified classes, renamed files — so agents can pull up the session behind any change in a fraction of the tokens a line-level view would cost.
      </p>

      <div className="terminal attr-terminal">
        <div className="attr-grid">
          <div className="attr-pane">
            <div className="terminal-bar">
              <span>graph</span>
              <span>opentraces graph --limit 4</span>
            </div>
            <div className="terminal-body attr-body">
              <GraphView />
            </div>
          </div>
          <div className="attr-pane attr-pane-right">
            <div className="terminal-bar">
              <span>blame</span>
              <span>opentraces blame ac019172</span>
            </div>
            <div className="terminal-body attr-body">
              <BlameView />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── graph: plain mono text, semantic colors only ──────────
function GraphView() {
  return (
    <div className="attr-mono">
      {GRAPH.map((line, i) => {
        if (line.kind === "trunk") {
          return (
            <div key={i} className="di attr-trunk">
              {line.text}
            </div>
          );
        }
        if (line.kind === "commit") {
          // Commit = orange ●, orange c:, default-text hash — visually strong anchor.
          const trunkOnly = line.tree.replace("●", "").replace(/ +$/, "");
          return (
            <div key={i} className="attr-row">
              <span className="attr-tree">
                <span className="di">{trunkOnly}</span>
                <span className="f">●</span>
                <span className="di">   </span>
              </span>
              <span className="f">c:</span>
              <span>{line.hash}</span>
              <span className="attr-ellip">{line.msg}</span>
              {line.cov && (
                <span className={line.cov === "100%" ? "ok" : "f"} style={{ marginLeft: "auto" }}>
                  {line.cov}
                </span>
              )}
            </div>
          );
        }
        // Session — dim tree branch, cyan s:, yellow hash.
        return (
          <div key={i} className="attr-row">
            <span className="di attr-tree">{line.tree}</span>
            <span className="n">s:</span>
            <span className="w">{line.hash}</span>
            {line.counts && <Counts c={line.counts} />}
            {line.noun && <span className="di">{line.noun}</span>}
            {line.detail && (
              <>
                <span className="di">│</span>
                <span className="w attr-ellip">{line.detail}</span>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── blame: commit header + spine linking to traces + files ─
function BlameView() {
  const totalLines = TRACES.reduce((s, t) => s + t.lines, 0);
  const totalPct = TRACES.reduce((s, t) => s + t.pct, 0);

  return (
    <div className="attr-mono">
      {/* Commit header: ● + commit HASH msg */}
      <div className="attr-row">
        <span className="attr-tree">
          <span className="f">●</span>
          <span className="di">  </span>
        </span>
        <span className="f" style={{ fontWeight: 500 }}>commit</span>
        <span>{COMMIT.hash}</span>
        <span className="attr-ellip">{COMMIT.msg}</span>
      </div>
      <div className="attr-row">
        <span className="attr-tree di">│  </span>
        <span className="di">
          <span className="n">{COMMIT.diffLines}</span> diff lines · <span className="n">{TRACES.length}</span> traces · <span className="n">{COMMIT.filesCount}</span> files
        </span>
      </div>
      <div className="attr-trunk di">│</div>

      {/* Trace blocks on the spine */}
      {TRACES.map((t, i) => {
        const isLast = i === TRACES.length - 1;
        const branch = isLast ? "╰" : "├";
        const cont = isLast ? " " : "│";
        return (
          <Fragment key={i}>
            <div className="attr-row">
              <span className="attr-tree">
                <span className="di">{branch}</span>
                <span className="n">◆</span>
              </span>
              <span className="n">s:</span>
              <span className="w">{t.hash}</span>
              <span className="di">{t.model}</span>
              <span className="di">·</span>
              <span className="n">{t.lines}/{COMMIT.diffLines}</span>
              <span className={t.pct === 100 ? "ok" : "f"} style={{ fontWeight: 500 }}>
                {t.pct}%
              </span>
            </div>
            {t.added && (
              <div className="attr-row">
                <span className="attr-tree di">{cont}  </span>
                <span className="ok">+</span>
                <span className="di">Added</span>
                <span className="attr-ellip">{t.added.join(", ")}</span>
              </div>
            )}
            {t.modified && (
              <div className="attr-row">
                <span className="attr-tree di">{cont}  </span>
                <span className="n">~</span>
                <span className="di">Modified</span>
                <span className="attr-ellip">{t.modified.join(", ")}</span>
              </div>
            )}
            {!isLast && <div className="attr-trunk di">│</div>}
          </Fragment>
        );
      })}

      {/* Total coverage summary */}
      <div style={{ marginTop: 10 }}>
        <span className="di">──── </span>
        <span className="n">{totalLines}/{COMMIT.diffLines}</span>
        <span className="di"> · </span>
        <span className="ok" style={{ fontWeight: 600 }}>{totalPct}%</span>
        <span className="di"> attributed ────</span>
      </div>

      <div className="di" style={{ marginTop: 10, marginBottom: 4 }}>files:</div>
      {FILES.map((f, i) => (
        <div key={i} className="attr-row">
          <span className="attr-ellip" style={{ flex: "1 1 auto" }}>{f.path}</span>
          <span className="n">{f.lines}L</span>
          <span className="di">
            (<span className="n">{f.edits}×</span> edits)
          </span>
        </div>
      ))}
    </div>
  );
}
