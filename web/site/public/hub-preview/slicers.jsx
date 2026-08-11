// ────────────────────────────────────────────────────────────────────────
// Slicers — the shared visual + data language for cutting a long trace
// into usable trajectories. Four grains:
//
//   S1 user-turn  · deterministic, capture-time · cut at each human ask
//   S2 change     · deterministic, capture-time · edit bursts on S1 bounds
//   S3 milestone  · model-labeled (local)       · closes on a verified outcome
//   S4 sub-goal   · model-labeled (local)       · S1 spine + internal pivots
//
// Model-labeled slicers run a small local model over the trace to name each
// slice — the model is part of the slice's provenance, so every surface that
// shows a model-cut slice attributes it (e.g. "milestone · qwen3-4b · local").
// ────────────────────────────────────────────────────────────────────────

const SLICERS = [
  {
    key: "user-turn", name: "User turn", short: "S1",
    tier: "deterministic", sig: "var(--c-user)",
    desc: "new trajectory at each human ask",
  },
  {
    key: "change", name: "Change", short: "S2",
    tier: "deterministic", sig: "var(--c-write)",
    desc: "edit bursts cut on user-turn boundaries",
  },
  {
    key: "milestone", name: "Milestone", short: "S3",
    tier: "model", model: "Qwen3-4B", modelShort: "qwen3-4b", runtime: "local", sig: "var(--c-exec)",
    desc: "closes on a verified outcome",
  },
  {
    key: "subgoal", name: "Sub-goal", short: "S4",
    tier: "model", model: "SmolLM3-3B", modelShort: "smollm3-3b", runtime: "local", sig: "var(--c-read)",
    desc: "user-turn spine + internal agent pivots",
  },
];
const slicerByKey = (key) => SLICERS.find((s) => s.key === key) || null;

// Which slicer cuts the rows of each dataset (provenance shown on the
// dataset pages — rows are extracted per-slice, not per-trace).
const DATASET_SLICERS = {
  "claude-eval-v3":  "milestone",
  "keystrokes-2026": "user-turn",
  "edge-traces":     "change",
  "rag-fixtures":    "subgoal",
  "shortcuts-bench": "user-turn",
};

// ── slicing (deterministic, over a class array) ─────────────────
// `classes` is the same lane vocabulary as the minimap / fingerprints:
// user · plan · think · read · exec · write. Returns [{ s, e, kind }].
function computeSlices(classes, key) {
  const n = classes.length;
  if (!n) return [];
  const cuts = new Set([0]);
  const addUserCuts = () => {
    for (let i = 1; i < n; i++) if (classes[i] === "user") cuts.add(i);
  };

  if (key === "user-turn") {
    addUserCuts();
  } else if (key === "change") {
    addUserCuts();
    // Edit bursts: runs of write steps, tolerating one read/exec gap inside.
    let i = 0;
    while (i < n) {
      if (classes[i] === "write") {
        let j = i, gap = 0, end = i;
        while (j < n) {
          if (classes[j] === "write") { end = j; gap = 0; j++; }
          else if (gap < 1 && (classes[j] === "exec" || classes[j] === "read")) { gap++; j++; }
          else break;
        }
        cuts.add(i);
        if (end + 1 < n) cuts.add(end + 1);
        i = end + 1;
      } else i++;
    }
  } else if (key === "milestone") {
    addUserCuts();
    // Close a milestone after a verification (exec) step settles.
    let last = 0;
    for (let i = 1; i < n - 1; i++) {
      if (classes[i] === "exec" && classes[i + 1] !== "exec" && i + 1 - last >= 5) {
        cuts.add(i + 1);
        last = i + 1;
      }
    }
  } else if (key === "subgoal") {
    addUserCuts();
    // Internal pivot: the agent turns back to reading after a work run.
    let last = 0;
    for (let i = 2; i < n; i++) {
      if (classes[i] === "read" && (classes[i - 1] === "write" || classes[i - 1] === "exec") && i - last >= 9) {
        cuts.add(i);
        last = i;
      }
    }
  } else {
    return [{ s: 0, e: n - 1, kind: "full" }];
  }

  const idx = [...cuts].filter((c) => c < n).sort((a, b) => a - b);
  return idx.map((s, k) => {
    const e = (idx[k + 1] !== undefined ? idx[k + 1] : n) - 1;
    let kind = key;
    if (key === "change") {
      let writes = 0;
      for (let i = s; i <= e; i++) if (classes[i] === "write") writes++;
      kind = writes >= 2 ? "burst" : "explore";
    }
    return { s, e, kind };
  });
}

// ── labels (derived from real steps when available) ─────────────
function sliceFirstLine(text, max = 64) {
  if (!text) return "";
  const t = String(text).trim().split(/\n/)[0].replace(/\s+/g, " ");
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
}
function sliceLabel(steps, slice, key) {
  if (!steps || !steps.length) return null;
  const inSlice = steps.slice(slice.s, slice.e + 1);
  const base = (p) => (p || "").split("/").pop();
  const files = [];
  let commitDesc = null, lastExecDesc = null, userLine = null;
  inSlice.forEach((st) => {
    if (st.role === "user" && !userLine) userLine = sliceFirstLine(st.content);
    (st.tool_calls || []).forEach((tc) => {
      const nm = tc.tool_name || "";
      const inp = tc.input || {};
      if (/edit|write/i.test(nm) && inp.file_path) {
        const f = base(inp.file_path);
        if (f && !files.includes(f)) files.push(f);
      }
      if (nm === "Bash") {
        if (/git\s+commit/.test(inp.command || "")) commitDesc = inp.description || commitDesc;
        else lastExecDesc = inp.description || lastExecDesc;
      }
    });
  });

  if (key === "user-turn") return userLine || "continuation";
  if (key === "change") {
    if (slice.kind === "burst") {
      const shown = files.slice(0, 2).join(", ");
      return files.length ? `burst: ${shown}${files.length > 2 ? ` +${files.length - 2}` : ""}` : "change burst";
    }
    return "explore / verify";
  }
  if (key === "milestone") {
    if (commitDesc) return sliceFirstLine(commitDesc, 56);
    if (files.length) return `Land changes to ${files[0]}${files.length > 1 ? ` +${files.length - 1}` : ""}`;
    if (lastExecDesc) return sliceFirstLine("Verify: " + lastExecDesc, 56);
    return userLine ? sliceFirstLine("Scope: " + userLine, 56) : "Milestone";
  }
  // subgoal
  if (userLine && steps[slice.s] && steps[slice.s].role === "user") return userLine;
  if (files.length) return `Work ${files[0]}${files.length > 1 ? ` +${files.length - 1}` : ""} to done`;
  if (lastExecDesc) return sliceFirstLine(lastExecDesc, 56);
  return "Internal pivot";
}

// ── seeded synthetic slices (for fingerprint-only surfaces) ─────
function seededSliceStart(seedStr, total, len) {
  let seed = Math.abs(String(seedStr).split("").reduce((a, c) => (a * 33 + c.charCodeAt(0)) | 0, 7));
  const max = Math.max(0, total - len);
  return max === 0 ? 0 : seed % max;
}

// ── slice taxonomy ─────────────────────────────────────────────
// A finite set of task types a slice typically captures. Assigned by the
// same local model that labels the slice; approximated here from the
// slice's step mix + tool text. Each type has a glyph — the symbolic
// representation of the slice when the band is minimised.
const SLICE_TYPES = {
  scope:    { name: "Scope",    desc: "understand / plan the ask" },
  explore:  { name: "Explore",  desc: "read & search the codebase" },
  build:    { name: "Build",    desc: "new capability" },
  fix:      { name: "Fix",      desc: "repair failing behavior" },
  refactor: { name: "Refactor", desc: "restructure, same behavior" },
  verify:   { name: "Verify",   desc: "tests / checks / screenshots" },
  polish:   { name: "Polish",   desc: "UI & copy fine-tuning" },
  ship:     { name: "Ship",     desc: "commit / push / land" },
};

function SliceGlyph({ type, size = 11 }) {
  const p = {
    scope:    <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" /></>,
    explore:  <><circle cx="11" cy="11" r="7" /><line x1="16.5" y1="16.5" x2="21" y2="21" /></>,
    build:    <><rect x="4" y="4" width="16" height="16" rx="2" /><line x1="12" y1="9" x2="12" y2="15" /><line x1="9" y1="12" x2="15" y2="12" /></>,
    fix:      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />,
    refactor: <><polyline points="17 1 21 5 17 9" /><path d="M3 11V9a4 4 0 0 1 4-4h14" /><polyline points="7 23 3 19 7 15" /><path d="M21 13v2a4 4 0 0 1-4 4H3" /></>,
    verify:   <><circle cx="12" cy="12" r="9" /><polyline points="8 12 11 15 16 9" /></>,
    polish:   <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />,
    ship:     <><line x1="12" y1="19" x2="12" y2="5" /><polyline points="5 12 12 5 19 12" /></>,
  }[type] || null;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{p}</svg>
  );
}

// Approximate the model's type assignment from the slice contents.
function classifySlice(steps, slice, classes) {
  const cls = classes.slice(slice.s, slice.e + 1);
  // A slice that is just a user turn (or user + immediate planning) is a
  // scoping slice — keyword heuristics don't apply to the human's words.
  if (cls.length <= 2 && cls[0] === "user") return "scope";
  const counts = {};
  cls.forEach((c) => { counts[c] = (counts[c] || 0) + 1; });
  let text = "", hasCommit = false, hasTest = false, newFile = false, edits = 0;
  for (let i = slice.s; i <= slice.e && i < (steps || []).length; i++) {
    const st = steps[i];
    if (st.role === "user") text += " " + (st.content || "");
    (st.tool_calls || []).forEach((tc) => {
      const nm = tc.tool_name || "", inp = tc.input || {};
      if (nm === "Write") newFile = true;
      if (/edit/i.test(nm)) edits++;
      if (nm === "Bash") {
        const c = (inp.command || "") + " " + (inp.description || "");
        if (/git\s+(commit|push)/.test(c)) hasCommit = true;
        if (/\b(test|pytest|jest|vitest|lint)\b/.test(c)) hasTest = true;
        text += " " + (inp.description || "");
      }
    });
  }
  const n = Math.max(1, cls.length);
  if (hasCommit) return "ship";
  if (/\b(fix|bug|error|fail|broken|crash)\b/i.test(text) && edits > 0) return "fix";
  if (/\b(polish|style|css|color|spacing|copy|label)\b/i.test(text) && edits > 0) return "polish";
  if (/\b(refactor|rename|clean up|restructure|move)\b/i.test(text)) return "refactor";
  if (hasTest && (counts.exec || 0) >= (counts.write || 0)) return "verify";
  if ((counts.write || 0) === 0 && (counts.read || 0) >= Math.max(1, counts.exec || 0)) return "explore";
  if ((counts.plan || 0) + (counts.think || 0) > n / 2) return "scope";
  if (newFile || edits > 0) return "build";
  return "explore";
}

// ── components ──────────────────────────────────────────────────

// Attribution chip: names the slicer and, for model-labeled tiers, the
// local model that produced the slice labels.
function SlicerChip({ slicer, prefix }) {
  const sl = typeof slicer === "string" ? slicerByKey(slicer) : slicer;
  if (!sl) return null;
  return (
    <span className="slicer-chip" style={{ "--sl-sig": sl.sig }}>
      <span className="slc-dot" />
      {prefix && <span className="slc-prefix">{prefix}</span>}
      <span className="slc-name">{sl.name.toLowerCase()}</span>
      {sl.tier === "model"
        ? <span className="slc-tier model mono">{sl.modelShort} · {sl.runtime}</span>
        : <span className="slc-tier det">deterministic</span>}
    </span>
  );
}

// The slicer rail lives under the trace minimap. ONE slicer is applied to
// a trace at a time — the project settings pick the default grain; the
// "Re-slice" menu applies another one on demand.
function SlicerRail({ classes, steps, slicerKey, onSlicerKey, activeSlice, onPickSlice, hoverSlice, onHoverSlice, focusedIdx, projectSlicer = "milestone" }) {
  const sl = slicerByKey(slicerKey);
  const slices = React.useMemo(
    () => (slicerKey === "full" ? [] : computeSlices(classes, slicerKey)),
    [classes, slicerKey]
  );
  const n = classes.length;

  // The slice "in focus": hovered wins, then the selected one, then whichever
  // contains the focused step — so the title tracks you as you move through.
  const followIdx = hoverSlice != null ? hoverSlice
    : activeSlice != null ? activeSlice
    : (focusedIdx != null && slices.length ? slices.findIndex((s) => focusedIdx >= s.s && focusedIdx <= s.e) : -1);
  const followSlice = followIdx >= 0 ? slices[followIdx] : null;
  const followLabel = followSlice ? sliceLabel(steps, followSlice, slicerKey) : null;

  const [menuOpen, setMenuOpen] = React.useState(false);
  const menuRef = React.useRef(null);
  React.useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e) => { if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false); };
    const onEsc = (e) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onEsc);
    };
  }, [menuOpen]);

  return (
    <div className="slicer-rail" data-active={activeSlice != null ? "1" : undefined}>
      <div className="sr-row">
        {/* the current slice's title leads; slicer + model are minor info right */}
        {sl && followSlice ? (
          <span className="sr-title" style={{ "--sl-sig": sl.sig }}>
            <span className="sr-title-t mono">T{followIdx}</span>
            <span className="sr-title-type" title={SLICE_TYPES[classifySlice(steps, followSlice, classes)].desc}>
              <SliceGlyph type={classifySlice(steps, followSlice, classes)} />
              {SLICE_TYPES[classifySlice(steps, followSlice, classes)].name}
            </span>
            <span className="sr-title-lbl">{followLabel || `steps ${followSlice.s}–${followSlice.e}`}</span>
            <span className="sr-title-span mono">{followSlice.s}–{followSlice.e}</span>
          </span>
        ) : (
          <span className="sr-title off"><span className="sr-title-lbl">{sl ? "Full trace — hover a slice" : "Full trace"}</span></span>
        )}

        <span className="sr-minor">
          {sl && (
            <>
              <span className="sr-opt-dot" style={{ "--sl-sig": sl.sig }} />
              <span className="sr-minor-name">{sl.name}</span>
              {sl.tier === "model" && <span className="mono">{sl.modelShort}</span>}
              {sl.tier === "model" ? <span className="sr-attr-rt">local</span> : <span className="sr-attr-rt">deterministic</span>}
              <span className="sr-minor-sep">·</span>
              <span>{slicerKey === projectSlicer ? "project default" : "on demand"}</span>
              <span className="sr-minor-sep">·</span>
              <span>{slices.length} {slices.length === 1 ? "trajectory" : "trajectories"}</span>
            </>
          )}
        </span>

        <div className="sr-menu-wrap" ref={menuRef}>
          <button className="sr-reslice" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((o) => !o)}>
            Re-slice
            <Icon name="chevron-down" size={11} />
          </button>
          {menuOpen && (
            <div className="bc-menu sr-menu" role="menu">
              <div className="bc-menu-group">
                <button className="bc-menu-item" role="menuitemradio" aria-current={slicerKey === "full" || undefined}
                  onClick={() => { onSlicerKey("full"); setMenuOpen(false); }}>
                  <span className="mi-label">Full trace</span>
                  <span className="mi-right">{slicerKey === "full" && <Icon name="check" size={13} className="mi-check" />}</span>
                </button>
              </div>
              <div className="bc-menu-group">
                {SLICERS.map((s) => (
                  <button key={s.key} className="bc-menu-item" role="menuitemradio" aria-current={slicerKey === s.key || undefined}
                    onClick={() => { onSlicerKey(s.key); setMenuOpen(false); }}>
                    <span className="sr-mi-dot" style={{ background: s.sig }} />
                    <span className="mi-label">
                      {s.name}
                      <span className="mi-sub">{s.tier === "model" ? `${s.modelShort} · local` : "deterministic"}</span>
                    </span>
                    <span className="mi-right">
                      {s.key === projectSlicer && <span className="mi-meta">default</span>}
                      {slicerKey === s.key && <Icon name="check" size={13} className="mi-check" />}
                    </span>
                  </button>
                ))}
              </div>
              <div className="sr-menu-foot">Default grain is set in project settings.</div>
            </div>
          )}
        </div>
      </div>

      {sl && slices.length > 0 && (
        <div className="sr-band" style={{ "--sl-sig": sl.sig }}>
          {slices.map((s, i) => {
            const isOn = activeSlice === i;
            const label = sliceLabel(steps, s, slicerKey);
            const type = classifySlice(steps, s, classes);
            return (
              <button
                key={i}
                className={"sr-seg" + (i % 2 ? " alt" : "") + (isOn ? " on" : "") + (hoverSlice === i && !isOn ? " hov" : "") + (activeSlice != null && !isOn ? " dim" : "")}
                style={{ "--w": Math.max(1, s.e - s.s + 1) }}
                title={isOn ? `T${i} · click to unselect` : `T${i} · ${SLICE_TYPES[type].name} · steps ${s.s}–${s.e}${label ? " · " + label : ""}`}
                aria-pressed={isOn}
                onMouseEnter={() => onHoverSlice && onHoverSlice(i)}
                onMouseLeave={() => onHoverSlice && onHoverSlice(null)}
                onClick={() => onPickSlice(isOn ? null : i)}
              >
                <span className="sr-seg-in">
                  <span className="sr-seg-ico"><SliceGlyph type={type} /></span>
                  <span className="sr-seg-t mono">T{i}</span>
                  <span className="sr-seg-span mono">{s.s}–{s.e}</span>
                  {label && <span className="sr-seg-lbl">{label}</span>}
                  {isOn && <span className="sr-seg-x" aria-hidden="true">✕</span>}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {sl && activeSlice != null && slices[activeSlice] && (
        <div className="sr-scope">
          <SlicerChip slicer={sl} prefix="viewing" />
          <span className="sr-scope-t mono">T{activeSlice}</span>
          <span className="sr-scope-span mono">steps {slices[activeSlice].s}–{slices[activeSlice].e} of {n}</span>
          {sliceLabel(steps, slices[activeSlice], slicerKey) && (
            <span className="sr-scope-lbl">{sliceLabel(steps, slices[activeSlice], slicerKey)}</span>
          )}
          <button className="sr-clear" onClick={() => onPickSlice(null)}>
            Clear <span className="mono">esc</span>
          </button>
        </div>
      )}
    </div>
  );
}

// Capsule slice context — a capsule never carries the full session; it
// carries ONE slice. Ghost track = the full trace; lit window = the slice.
function CapsuleSliceContext({ cap, slicerKey = "user-turn" }) {
  const sl = slicerByKey(slicerKey);
  const total = cap.manifest.trajectory.steps;
  const fp = cap.fingerprint || [];
  const len = fp.length;
  const start = seededSliceStart(cap.id, total, len);
  const tone = {
    user: "var(--c-user)", plan: "var(--c-plan)", think: "var(--c-think)",
    read: "var(--c-read)", exec: "var(--c-exec)", write: "var(--c-write)",
  };
  return (
    <div className="cap-slice-ctx">
      <div className="csc-head">
        <span className="csc-k">Captured slice</span>
        <SlicerChip slicer={sl} prefix="cut by" />
        <span className="csc-span mono">steps {start}–{start + len - 1} of {total}</span>
      </div>
      <div className="csc-track" aria-hidden="true">
        <span className="csc-ghost" style={{ flexGrow: Math.max(0, start) }} />
        <span className="csc-window" style={{ flexGrow: len }}>
          {fp.map((c, i) => <i key={i} style={{ background: tone[c] || "var(--c-plan)" }} />)}
        </span>
        <span className="csc-ghost" style={{ flexGrow: Math.max(0, total - start - len) }} />
      </div>
      <div className="csc-foot">Narrow by default — the other {total - len} steps of the session never leave the machine.</div>
    </div>
  );
}

Object.assign(window, {
  SLICERS, slicerByKey, DATASET_SLICERS,
  SLICE_TYPES, SliceGlyph, classifySlice,
  computeSlices, sliceLabel, seededSliceStart,
  SlicerChip, SlicerRail, CapsuleSliceContext,
});
