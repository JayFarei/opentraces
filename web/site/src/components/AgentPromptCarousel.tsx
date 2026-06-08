"use client";

import { useEffect, useState } from "react";
import { AGENT_GLYPHS } from "./AgentGlyphs";

// Emulates a coding agent's composer: types one opentraces ask, holds, clears,
// and moves to the next. Each prompt is segmented so the OpenTraces concept /
// trigger words (the vocabulary the agent recognises) get a shimmer accent.
// The agent logo rotates with each ask to reinforce that any agent drives it.
type Seg = { t: string; trigger?: boolean };

const PROMPTS: Seg[][] = [
  [{ t: "install " }, { t: "opentraces", trigger: true }],
  [{ t: "sync my " }, { t: "bucket", trigger: true }, { t: " with HF" }],
  [{ t: "create a " }, { t: "dataset", trigger: true }, { t: " on my skill <name>" }],
  [{ t: "summarize the " }, { t: "intent", trigger: true }, { t: " of this pull request" }],
  [{ t: "create a " }, { t: "trace capsule", trigger: true }, { t: " over this bug" }],
];

const FULL = PROMPTS.map((segs) => segs.map((s) => s.t).join(""));

const TYPE_MS = 55;
const DELETE_MS = 24;
const HOLD_MS = 1600;
const CLEARED_MS = 420;
const REDUCED_ROTATE_MS = 2600;

// Render the first `count` typed characters, wrapping trigger words so they
// can shimmer (only the portion typed so far is shown).
function renderTyped(segs: Seg[], count: number) {
  let remaining = count;
  const out: React.ReactNode[] = [];
  segs.forEach((seg, i) => {
    if (remaining <= 0) return;
    const shown = seg.t.slice(0, remaining);
    remaining -= shown.length;
    out.push(
      seg.trigger ? (
        <span key={i} className="agent-trigger">{shown}</span>
      ) : (
        <span key={i}>{shown}</span>
      ),
    );
  });
  return out;
}

export default function AgentPromptCarousel() {
  const [idx, setIdx] = useState(0);
  const [sub, setSub] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduced(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    if (reduced) {
      const t = setTimeout(() => setIdx((i) => (i + 1) % PROMPTS.length), REDUCED_ROTATE_MS);
      return () => clearTimeout(t);
    }

    const len = FULL[idx].length;
    let delay = deleting ? DELETE_MS : TYPE_MS;
    if (!deleting && sub === len) delay = HOLD_MS;
    if (deleting && sub === 0) delay = CLEARED_MS;

    const t = setTimeout(() => {
      if (!deleting && sub < len) {
        setSub(sub + 1);
      } else if (!deleting && sub === len) {
        setDeleting(true);
      } else if (deleting && sub > 0) {
        setSub(sub - 1);
      } else {
        setDeleting(false);
        setIdx((i) => (i + 1) % PROMPTS.length);
      }
    }, delay);
    return () => clearTimeout(t);
  }, [idx, sub, deleting, reduced]);

  const count = reduced ? FULL[idx].length : sub;
  const Glyph = AGENT_GLYPHS[idx % AGENT_GLYPHS.length];

  return (
    <div className="agent-bar" aria-hidden="true">
      <div className="agent-bar-box">
        <div className="agent-bar-input">
          <span className="agent-bar-caret">›</span>
          <span className="agent-bar-text">{renderTyped(PROMPTS[idx], count)}</span>
          <span className="agent-bar-cursor" />
        </div>
      </div>
      <div className="agent-bar-meta">
        <Glyph className="agent-bar-logo" />
        <span className="agent-bar-dim">~/my-project</span>
      </div>
    </div>
  );
}
