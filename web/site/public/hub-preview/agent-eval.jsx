// ─────────────────────────────────────────────────────────────
// Agent eval harness — a batch of intent queries (with phrasing
// perturbations) run against the live CLUI agent, each scored on
// REAL app state afterwards: route reached, theme flipped, artifact
// created. This measures "can you operate this app by talking to it".
//
// Open with ⌥E, the `agent-eval` route, or window.__otEval.open().
// Cases run sequentially with a gap to respect the model rate limit.
// ─────────────────────────────────────────────────────────────

const OT_EVAL_CASES = [
  {
    id: "datasets",
    label: "See all datasets",
    prompts: [
      "show me all the datasets",
      "can I see every dataset we have?",
      "datasets pls",
    ],
    expect: { route: { view: "datasets-index" } },
  },
  {
    id: "trail",
    label: "Trail visualization",
    prompts: [
      "show me the trail for the most recent trace",
      "visualize the latest trace",
    ],
    expect: { route: { view: "trace" }, tab: "trail" },
  },
  {
    id: "pr",
    label: "Open latest PR",
    prompts: [
      "open the last PR on opentraces",
      "what's the newest pull request?",
    ],
    expect: { route: { view: "repo", repoChild: "pulls" } },
  },
  {
    id: "capsules",
    label: "Latest capsule",
    prompts: [
      "latest capsule overview",
      "show me my capsules",
    ],
    expect: { route: { view: "capsules" } },
  },
  {
    id: "spotlight",
    label: "Semantic search",
    prompts: [
      "search my traces for auth failures",
      "find traces about login errors",
    ],
    expect: { route: { view: "spotlight" } },
  },
  {
    id: "projects",
    label: "Browse repos",
    prompts: [
      "show me all my projects",
      "list the repositories",
    ],
    expect: { route: { view: "projects-index" } },
  },
  {
    id: "artifacts",
    label: "Artifacts index",
    prompts: [
      "show me my artifacts",
      "where are the dashboards you made me?",
    ],
    expect: { route: { view: /^artifact/ } },
  },
  {
    id: "theme",
    label: "Toggle theme (page action)",
    prompts: [
      "switch the theme using the page controls",
    ],
    expect: { themeFlip: true },
  },
  {
    id: "compare",
    label: "Compare traces",
    prompts: [
      "compare two traces side by side",
    ],
    expect: { route: { view: "compare" } },
  },
  {
    id: "artifact-create",
    label: "Generate a dashboard (slow)",
    prompts: [
      "build me a small dashboard of dataset sync state",
    ],
    expect: { artifactDelta: 1 },
    slow: true,
  },
];

function otEvalCheck(expect, before) {
  const r = window.__otRoute || {};
  const fails = [];
  if (expect.route) {
    Object.entries(expect.route).forEach(([k, v]) => {
      const cur = r[k];
      const ok = v instanceof RegExp ? v.test(String(cur || "")) : cur === v;
      if (!ok) fails.push(k + "=" + JSON.stringify(cur) + " (wanted " + String(v) + ")");
    });
  }
  if (expect.tab && r.activeTab !== expect.tab) fails.push("tab=" + r.activeTab + " (wanted " + expect.tab + ")");
  if (expect.themeFlip) {
    const now = document.documentElement.getAttribute("data-theme");
    if (now === before.theme) fails.push("theme unchanged (" + now + ")");
  }
  if (expect.artifactDelta) {
    const now = OtArtifacts.get().length;
    if (now - before.artifacts < expect.artifactDelta) fails.push("no artifact created");
  }
  return fails;
}

function AgentEvalOverlay() {
  const [open, setOpen] = React.useState(false);
  const [sel, setSel] = React.useState(() => {
    const m = {};
    OT_EVAL_CASES.forEach(c => c.prompts.forEach((p, i) => { m[c.id + ":" + i] = !c.slow; }));
    return m;
  });
  const [results, setResults] = React.useState({}); // key → {status, note, ms}
  const [running, setRunning] = React.useState(false);
  const [gap, setGap] = React.useState(8);
  const stopRef = React.useRef(false);

  React.useEffect(() => {
    const onKey = (e) => { if (e.altKey && (e.code === "KeyE")) { e.preventDefault(); setOpen(o => !o); } };
    window.addEventListener("keydown", onKey);
    window.__otEval = { open: () => setOpen(true) };
    return () => { window.removeEventListener("keydown", onKey); delete window.__otEval; };
  }, []);

  const keys = [];
  OT_EVAL_CASES.forEach(c => c.prompts.forEach((p, i) => keys.push({ key: c.id + ":" + i, c, p, i })));

  const run = async () => {
    const D = window.__cluiDrive;
    if (!D) { alert("Open the app first — the Claude island isn't mounted."); return; }
    setRunning(true);
    stopRef.current = false;
    setResults({});
    D.setPermMode("auto");
    for (const { key, c, p } of keys) {
      if (stopRef.current) break;
      if (!sel[key]) continue;
      setResults(r => ({ ...r, [key]: { status: "running" } }));
      // reset to home so every case starts from the same state
      try { OtRegistry.open("traces"); } catch (e) {}
      await new Promise(res => setTimeout(res, 500));
      const before = {
        theme: document.documentElement.getAttribute("data-theme"),
        artifacts: OtArtifacts.get().length,
      };
      const t0 = Date.now();
      let note = "", status = "pass";
      try {
        const reply = await D.send(p);
        await new Promise(res => setTimeout(res, 1200)); // deep-link timers
        const fails = otEvalCheck(c.expect, before);
        if (fails.length) { status = "fail"; note = fails.join("; "); }
        else note = String(reply || "").slice(0, 90);
      } catch (e) {
        status = "fail"; note = String(e && e.message || e).slice(0, 120);
      }
      setResults(r => ({ ...r, [key]: { status, note, ms: Date.now() - t0 } }));
      await new Promise(res => setTimeout(res, gap * 1000));
    }
    D.setPermMode("ask");
    setRunning(false);
  };

  if (!open) return null;
  const done = Object.values(results).filter(r => r.status === "pass" || r.status === "fail");
  const passed = done.filter(r => r.status === "pass").length;

  return (
    <div className="oteval" data-screen-label="Agent eval harness">
      <div className="oteval-head">
        <span className="oteval-title">Agent eval — intent → operation</span>
        <span className="oteval-score mono">{done.length ? passed + "/" + done.length : ""}</span>
        <button className="oteval-x" onClick={() => { stopRef.current = true; setOpen(false); }}>✕</button>
      </div>
      <div className="oteval-sub">Each prompt runs against the live agent; pass = the app actually reached the expected state. Perturbed phrasings test robustness.</div>
      <div className="oteval-rows">
        {keys.map(({ key, c, p, i }) => {
          const res = results[key];
          return (
            <label className="oteval-row" key={key} data-status={res ? res.status : "idle"}>
              <input type="checkbox" checked={!!sel[key]} disabled={running} onChange={(e) => setSel(s => ({ ...s, [key]: e.target.checked }))}></input>
              <span className="oteval-dot"></span>
              <span className="oteval-case">
                <span className="oteval-lbl">{c.label}{i > 0 ? " · v" + (i + 1) : ""}</span>
                <span className="oteval-prompt">“{p}”</span>
                {res && res.note && <span className="oteval-note">{res.note}</span>}
              </span>
              <span className="oteval-ms mono">{res && res.ms ? Math.round(res.ms / 100) / 10 + "s" : ""}</span>
            </label>
          );
        })}
      </div>
      <div className="oteval-foot">
        <label className="oteval-gap">gap
          <input type="number" min="4" max="30" value={gap} disabled={running} onChange={e => setGap(Math.max(4, Number(e.target.value) || 8))}></input>s
        </label>
        {running
          ? <button className="oteval-run stop" onClick={() => { stopRef.current = true; }}>Stop</button>
          : <button className="oteval-run" onClick={run}>Run selected</button>}
      </div>
    </div>
  );
}

window.AgentEvalOverlay = AgentEvalOverlay;
