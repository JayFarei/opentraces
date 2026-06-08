import { Fragment, ReactNode, CSSProperties } from "react";
import SectionRule from "./SectionRule";

// One protagonist session (tr_6606fc1f) carried across all three lanes:
// its patches land in a commit, then are followed through Git history.

// ── Lane 1 · Snapshots & Patches — snapshot at each step, hunk per write ──
type SCount = { add?: number; mod?: number; del?: number };
type SPatch = { snap: string; file: string } & SCount;
const PATCHES: SPatch[] = [
  { snap: "a83f", file: "graph_api.py", add: 14, mod: 4 },
  { snap: "b2f1", file: "PushModal.tsx", add: 30 },
  { snap: "c4d8", file: "server.py", mod: 12, del: 2 },
];

// ── Lane 2 · Git Anchor — share of the commit that resolves to each session ──
const COMMIT = { hash: "ac019172", msg: "feat(viewer): hover actions", diffLines: 94 };
type TraceRow = { hash: string; pct: number };
const TRACES: TraceRow[] = [
  { hash: "6606fc1f", pct: 48 },
  { hash: "fe8fe3fd", pct: 32 },
  { hash: "5baac494", pct: 20 },
];

// ── Lane 3 · Survival — followed through rebases, reverts, rewrites ──
type SurvivalRow = { glyph: string; cls: string; trace: string; state: string };
const SURVIVAL: SurvivalRow[] = [
  { glyph: "✓", cls: "ok", trace: "6606fc1f", state: "alive_on_path" },
  { glyph: "↻", cls: "n", trace: "fe8fe3fd", state: "alive_moved" },
  { glyph: "≈", cls: "s", trace: "5baac494", state: "alive_transformed" },
  { glyph: "⤳", cls: "w", trace: "c42a9d10", state: "repaired" },
  { glyph: "✗", cls: "er", trace: "91bb4027", state: "reverted" },
  { glyph: "?", cls: "di", trace: "a17e02b8", state: "unknown" },
];

// The questions this lineage answers — surfaced above the mechanism.
const QUESTIONS = [
  { q: "How much did we spend on code that never shipped?", a: "Sum the tokens and wall-clock of every session whose patches ended reverted or lost.", color: "var(--c-error)" },
  { q: "What produced this line?", a: "Reverse-blame any file:line back to the session, prompt, and diff behind it.", color: "var(--c-write)" },
  { q: "Which sessions introduced this bug?", a: "Walk a bad commit back to its sessions — then fix the process, not just the line.", color: "var(--c-push)" },
  { q: "Which sessions reached main?", a: "Filter every session by survival to compile an outcome-labeled dataset.", color: "var(--c-git)" },
];

function Counts({ c }: { c: SCount }) {
  const items: ReactNode[] = [];
  if (c.add) items.push(<span key="a" className="ok">+{c.add}</span>);
  if (c.mod) items.push(<span key="m" className="n">~{c.mod}</span>);
  if (c.del) items.push(<span key="d" className="er">-{c.del}</span>);
  return (
    <span className="attr-counts">
      {items.map((node, i) => (
        <Fragment key={i}>
          {i > 0 && " "}
          {node}
        </Fragment>
      ))}
    </span>
  );
}

type LaneProps = { num: number; name: string; tag: string; cmd: string; color: string; children: ReactNode };
function Lane({ num, name, tag, cmd, color, children }: LaneProps) {
  return (
    <div className="lane" style={{ "--lane-c": color } as CSSProperties}>
      <div className="lane-bar">
        <span className="lane-tag">
          <span className="lane-num">{num}</span>
          <span className="lane-name">{name}</span>
        </span>
        <span className="lane-vocab">{tag}</span>
      </div>
      <div className="terminal-body attr-body attr-mono">{children}</div>
      <div className="lane-cmd">
        <span className="di">$ opentraces </span>
        {cmd}
      </div>
    </div>
  );
}

function Arrow({ label, from, to }: { label: string; from: string; to: string }) {
  return (
    <div className="lane-arrow" aria-hidden="true" style={{ "--lk-from": from, "--lk-to": to } as CSSProperties}>
      <span className="lane-arrow-chev">→</span>
      <span className="lane-arrow-label">{label}</span>
    </div>
  );
}

export default function Attribution() {
  return (
    <section>
      <SectionRule label="lineage" />
      <div className="section-title">What did this trace do?</div>
      <p className="section-sub" style={{ maxWidth: 700, marginBottom: 22 }}>
        Every change carries the session that made it. Anchor it to the commit that accepted it, follow it through the rebases and reverts after, and you can ask questions{" "}
        <strong style={{ color: "var(--text)" }}>git blame</strong>{" "}
        can&apos;t:
      </p>

      <div className="qgrid">
        {QUESTIONS.map((it) => (
          <div key={it.q} className="qcard" style={{ "--qc": it.color } as CSSProperties}>
            <div className="qcard-q">
              <span className="qcard-dot" aria-hidden="true" />
              {it.q}
            </div>
            <p className="qcard-a">{it.a}</p>
          </div>
        ))}
      </div>

      <p className="lanes-lead">
        <strong style={{ color: "var(--text-secondary)" }}>Behind every answer is one trail</strong> — the snapshots and patches in a session, the git anchor that accepted them, and what survived.
      </p>

      <div className="lanes">
        {/* Lane 1 — Snapshots & Patches */}
        <Lane num={1} name="snapshots & patches" tag="session worktree" cmd="trace map --bursts" color="var(--c-write)">
          <div className="attr-row">
            <span className="n">tr:</span>
            <span className="w">6606fc1f</span>
            <span className="di">claude-code</span>
          </div>
          <div className="attr-trunk di">│</div>
          {PATCHES.map((p, i) => (
            <div key={p.snap} className="attr-row">
              <span className="attr-tree di">{i === PATCHES.length - 1 ? "╰" : "├"} ~</span>
              <span className="w">{p.snap}</span>
              <span className="n">◆</span>
              <span className="attr-ellip">{p.file}</span>
              <Counts c={p} />
            </div>
          ))}
          <div className="lane-note">snapshot per step · patch = one hunk</div>
        </Lane>

        <Arrow label="correlate" from="var(--c-write)" to="var(--c-push)" />

        {/* Lane 2 — Git Anchor */}
        <Lane num={2} name="git anchor" tag="the bridge" cmd="trail blame commit" color="var(--c-push)">
          <div className="attr-row">
            <span className="f">●</span>
            <span>{COMMIT.hash}</span>
            <span className="attr-ellip di">{COMMIT.msg}</span>
          </div>
          <div className="attr-trunk di">│</div>
          {TRACES.map((t, i) => (
            <div key={t.hash} className="attr-row">
              <span className="attr-tree">
                <span className="di">{i === TRACES.length - 1 ? "╰" : "├"}</span>
                <span className="n">◆</span>
              </span>
              <span className="w">{t.hash}</span>
              <span className={t.pct === 100 ? "ok" : "f"} style={{ fontWeight: 500, marginLeft: "auto" }}>{t.pct}%</span>
            </div>
          ))}
          <div className="attr-row" style={{ marginTop: 4 }}>
            <span className="di">────</span>
            <span className="ok" style={{ fontWeight: 600 }}>100%</span>
            <span className="di">of {COMMIT.diffLines} lines</span>
          </div>
          <div className="lane-note">share of the diff, by hash then structure</div>
        </Lane>

        <Arrow label="follow" from="var(--c-push)" to="var(--c-git)" />

        {/* Lane 3 — Survival */}
        <Lane num={3} name="survival" tag="git's verdict" cmd="trail track 6606fc1f" color="var(--c-git)">
          {SURVIVAL.map((s) => (
            <div key={s.trace} className="attr-row">
              <span className={`${s.cls} attr-glyph`}>{s.glyph}</span>
              <span className="w">{s.trace}</span>
              <span className={`${s.cls} attr-ellip`} style={{ fontWeight: 500 }}>{s.state}</span>
            </div>
          ))}
          <div className="lane-note">eight outcome states across history</div>
        </Lane>
      </div>

      {/* Payoff — prove what sessions did → improve the process + label outcomes */}
      <p className="lane-coda">
        <strong style={{ color: "var(--text)" }}>Prove it, then improve the process.</strong> Show which sessions shipped, which were reverted, and which introduced a bug or left tech debt, and you have the
        evidence to change what your agents do next — and to label outcome data after the fact. Run the security tools first and the trail itself becomes the label, with no in-session annotation.
      </p>
    </section>
  );
}
