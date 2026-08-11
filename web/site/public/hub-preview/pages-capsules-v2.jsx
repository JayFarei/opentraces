// pages-capsules-v2.jsx — Replays (capsules), recut witness-first.
//   · Witness (rewatch, anonymous from the URL) is shipped.
//   · Reenact is an earned upgrade — account-gated, on our box, gated on the
//     fix-verification test; a recipient's reenactment accrues THEIR custody.
//   · The honest badge is the card's primary fact — and today every honest
//     capsule reports floor trust and refuses "reproducible". The UI shows
//     that refusal; it never fakes a live verdict.
//   · Verdict-vs-current-main is horizon (rides the bench runner).
//   · No views/pulls/subscribers counters. Watchlist stays.

// Per-capsule honesty overlay: badge facets, dependency modes, witness cast.
const CAPV2 = {
  "cap-1": {
    deps: { provider: "recorded", fs: "sealed", net: "omitted", clock: "simulated" },
    cast: {
      dur: 20,
      frames: [
        { t: 0,  lines: [{ tone: "cmd", text: "$ ot capsule witness cap_7f3a9c2   # anonymous — no account" }] },
        { t: 3,  lines: [{ tone: "dim", text: "seal digest ok · trajectory 142 steps · deps: provider=recorded fs=sealed net=omitted" }] },
        { t: 7,  lines: [{ tone: "dim", text: "step 139  next build --experimental (edge)" }] },
        { t: 12, lines: [{ tone: "red", text: "step 142  ReferenceError: WebAssembly.instantiate is not a function" }] },
        { t: 16, lines: [{ tone: "out", text: "sealed-at verdict: fail @ a1b2c3d — the film is the fact; nothing is re-run" }] },
      ],
    },
  },
  "cap-2": {
    deps: { provider: "recorded", fs: "sealed", net: "recorded" },
    cast: {
      dur: 16,
      frames: [
        { t: 0,  lines: [{ tone: "cmd", text: "$ ot capsule witness cap_b41e0d8" }] },
        { t: 3,  lines: [{ tone: "dim", text: "seal digest ok · 76 steps · SSE chunks replayed from seal (212)" }] },
        { t: 8,  lines: [{ tone: "red", text: "step 76  stream ended at 4096 tokens · finish_reason=null" }] },
        { t: 13, lines: [{ tone: "out", text: "sealed-at verdict: fail @ 9e02f7a" }] },
      ],
    },
  },
  "cap-3": {
    deps: { provider: "mocked", fs: "sealed", net: "omitted" },
    cast: null, // no cast in the seal — said out loud
  },
};
const capV2 = (id) => CAPV2[id] || { deps: { fs: "sealed" }, cast: null };

// The honest badge: reproducible · gradable · scoped · sandboxed.
// Floor trust refuses "reproducible" — the refusal is rendered, not hidden.
function CapsuleBadge({ cap, big }) {
  const facets = [
    { k: "reproducible", state: "refused", hint: "Floor trust: the box recipe is unattested, so this capsule refuses the claim. An attested reenactment can earn it — the stamp never relabels upward on its own." },
    { k: "gradable", state: "yes", hint: "A verifier is sealed with the trajectory; the failure is assertable, not anecdotal." },
    { k: "scoped", state: "yes", hint: "Every dependency carries an honest mode — nothing outside the seal leaks in." },
    { k: "sandboxed", state: "container", hint: "Derived sandbox_tier of the sealing run. Never relabelled upward." },
  ];
  return (
    <span className={"capv2-badge" + (big ? " big" : "")}>
      {facets.map(f => (
        <span key={f.k} className={"capv2-facet cf-" + (f.state === "refused" ? "refused" : "ok")} title={f.k + " — " + f.hint}>
          {f.state === "refused"
            ? <s>{f.k}</s>
            : <>{f.k}{f.state !== "yes" && <span className="cf-val mono">{f.state}</span>}</>}
        </span>
      ))}
      <span className="capv2-floor mono" title="Today every honest capsule reports floor trust — the refusal above is the honest reading of it.">floor trust</span>
    </span>
  );
}

function DepModes({ deps }) {
  const TONE = { live: "warn", recorded: "ok", mocked: "dim", simulated: "dim", omitted: "dim", sealed: "ok" };
  return (
    <span className="capv2-deps">
      {Object.entries(deps).map(([k, v]) => (
        <span key={k} className={"capv2-dep dm-" + (TONE[v] || "dim")} title={k + " carried as " + v + " — dependency modes are stated, never assumed."}>
          <span className="mono">{k}</span>={v}
        </span>
      ))}
    </span>
  );
}

function CapsuleV2Card({ cap, onOpen }) {
  const w = deriveWatch(cap);
  return (
    <button className="capv2-card" onClick={() => onOpen(cap.id)}>
      <div className="capv2-card-top">
        <CapsuleSeal lane={cap.fail.lane} size={30} />
        <div className="capv2-card-titles">
          <div className="capv2-title">{cap.title}</div>
          <div className="capv2-sub">
            <span className="mono">{cap.cid}</span>
            <span className="capv2-repo">{cap.repo}{cap.issue && cap.issue.number != null ? " · #" + cap.issue.number : ""}</span>
            <span className="capv2-when">{cap.publishedAt}</span>
          </div>
        </div>
        <span className="capv2-sealedat" title="The verdict at seal time. Nothing sealed ever changes; a re-run would be a new run.">
          <VerdictPill v={cap.replays[0].outcome} /> <span className="mono">@{cap.git.sha}</span>
        </span>
      </div>
      <CapsuleBadge cap={cap} />
      <div className="capv2-card-foot">
        <span className="capv2-witness" title="Witness — rewatch the sealed film. Anonymous from the URL; no account.">
          <Icon name="play" size={11} /> witness
        </span>
        <span className="capv2-reenact locked" title="Reenact — earned upgrade. Account-gated, runs on our box, gated on the fix-verification test. A recipient's reenactment accrues THEIR custody." onClick={(e) => e.stopPropagation()}>
          <Icon name="lock" size={11} /> reenact
        </span>
        <FacetChip facet="observed" small />
        {w && <span className="capv2-watch" title={"On the client watchlist — goal: " + w.goal}><Icon name="eye" size={11} /> watched</span>}
      </div>
    </button>
  );
}

function CapsulesV2Page({ onOpenCapsule, onSelectTrace }) {
  const caps = CAPSULES.map(c => withTriage(c));
  const watched = caps.map(c => ({ cap: c, w: deriveWatch(c) })).filter(x => x.w);
  return (
    <div className="landing landing-page v2-page">
      <V2Hero
        plain="Capsules"
        tech="capsule"
        subtitle="Sealed runs that travel. Anyone can witness the film from the URL; reenacting it is an earned, account-gated upgrade on our box. Nothing sealed ever changes."
        scope={`${caps.length} capsules · ${watched.length} on the watchlist`}
        actions={<div className="ph-action-group"><ToolBtn icon="capsule" label="Seal a capsule" primary /></div>}
      />

      {watched.length > 0 && (
        <div className="ev-card capv2-watchlist">
          <div className="ev-card-head">Client watchlist <span className="ev-card-hint">what your agents are blocked on — the client-side render of the exchange</span></div>
          {watched.map(({ cap, w }) => (
            <button key={cap.id} className="capv2-watch-row" onClick={() => onOpenCapsule(cap.id)}>
              <span className={"capv2-watch-state ws-" + (cap.lifecycle === "resolved" || cap.lifecycle === "unblocked" ? "ok" : "wait")} />
              <span className="capv2-watch-goal">{w.goal}</span>
              {w.workaround && <span className="capv2-watch-wa mono" title="stop-gap while blocked">workaround: {w.workaround}</span>}
              <span className="capv2-watch-life mono">{cap.lifecycle}</span>
            </button>
          ))}
        </div>
      )}

      <div className="capv2-grid">
        {caps.map(cap => <CapsuleV2Card key={cap.id} cap={cap} onOpen={onOpenCapsule} />)}
      </div>
    </div>
  );
}

// ── Reenact composer — the counterfactual seat. The seal fixes the episode;
// a reenactment can swap axes (environment deps, harness, model). Zero
// swaps = a fidelity check; any swap = a counterfactual run.
function ReenactComposer({ cap, deps }) {
  const sealedModel = cap.agent.model;
  const sealedHarness = cap.agent.name;
  const [model, setModel] = React.useState(sealedModel);
  const [harness, setHarness] = React.useState(sealedHarness);
  const [provider, setProvider] = React.useState(deps.provider || "recorded");
  const models = [...new Set([sealedModel, "Sonnet 4.5", "GPT-5.3 Codex", "Gemini 3 Pro"])];
  const harnesses = [...new Set([sealedHarness, "Claude Code", "Codex", "Cursor"])];
  const providers = [...new Set([deps.provider || "recorded", "recorded", "live", "mocked"])];
  const changes =
    (model !== sealedModel ? 1 : 0) +
    (harness !== sealedHarness ? 1 : 0) +
    (provider !== (deps.provider || "recorded") ? 1 : 0);
  const Row = ({ label, value, sealed, options, onChange }) => (
    <div className="rc-row" data-changed={value !== sealed}>
      <span className="rc-k">{label}</span>
      <select className="rc-select mono" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(o => <option key={o} value={o}>{o}{o === sealed ? "  · sealed" : ""}</option>)}
      </select>
    </div>
  );
  return (
    <div className="ev-card capv2-reenact-card">
      <div className="ev-card-head">Reenact in an arena <span className="ev-card-hint">swap an axis to pose a counterfactual</span></div>
      <div className="rc-rows">
        <Row label="model" value={model} sealed={sealedModel} options={models} onChange={setModel} />
        <Row label="harness" value={harness} sealed={sealedHarness} options={harnesses} onChange={setHarness} />
        <Row label="provider dep" value={provider} sealed={deps.provider || "recorded"} options={providers} onChange={setProvider} />
      </div>
      <div className={"rc-verdict" + (changes ? " cf" : "")}>
        {changes === 0
          ? "Exact reenactment — a fidelity check of the seal."
          : changes + (changes === 1 ? " axis" : " axes") + " changed — a counterfactual run, graded against the sealed episode."}
      </div>
      <button className="capv2-gate-btn" disabled title="Sign in to request a reenactment — it runs on our box and is gated on the fix-verification test.">
        <Icon name="lock" size={12} /> {changes ? "Run counterfactual" : "Reenact exactly"}
      </button>
      <div className="capv2-gate rc-gates">
        <div className="capv2-gate-row"><Icon name="lock" size={12} /> account required · witness stays anonymous</div>
        <div className="capv2-gate-row"><Icon name="box" size={12} /> runs on our box, never yours</div>
        <div className="capv2-gate-row"><Icon name="shield" size={12} /> gated on the fix-verification test</div>
      </div>
      <div className="tl-note">Your reenactment accrues <b>your</b> custody; the seal never changes — every run is a new run_id, attested as an arena label.</div>
    </div>
  );
}

// ── Detail — a rendering of the frozen record, witness-first ────
function CapsuleV2Detail({ capId, onBack, onSelectTrace, trace }) {
  const cap = withTriage(CAPSULES.find(c => c.id === capId) || CAPSULES[0]);
  const x = capV2(cap.id);
  const [view, setView] = React.useState("witness");
  const [fi, setFi] = React.useState(0);
  const classes = React.useMemo(() => (trace ? trace.steps.map(classifyStep) : []), [trace]);
  const allFilters = { user: true, plan: true, think: true, checkpoint: true, file_edits: true, bash: true, read: true, other: true };
  const evLike = {
    id: "cap-witness-" + cap.id,
    rewatchable: !!x.cast,
    cast: x.cast,
  };
  return (
    <div className="landing landing-page v2-page ev-detail" data-screen-label={"capsule-" + cap.id}>
      <header className="ev-d-head">
        <CapsuleSeal lane={cap.fail.lane} size={40} />
        <div className="ev-d-titles">
          <h1>{cap.title}</h1>
          <div className="ev-d-meta">
            <span className="capv2-sealedat"><VerdictPill v={cap.replays[0].outcome} /> <span className="mono">sealed @{cap.git.sha}</span></span>
            <FacetChip facet="observed" />
            <AddressLink addr={"tr-" + cap.cid.slice(4) + ":" + cap.fail.step} onOpen={onSelectTrace} />
            <span className="capv2-anon mono" title="This exact page is what an anonymous witness sees from the URL."><Icon name="globe" size={12} /> witness view · anonymous</span>
          </div>
          <div className="ev-d-meta"><CapsuleBadge cap={cap} big /></div>
          <div className="ev-d-meta"><DepModes deps={x.deps} /></div>
        </div>
        <span className="ev-d-when mono">{cap.publishedAt}</span>
      </header>

      <div className="ev-d-grid">
        <div className="ev-d-main">
          <div className="bhub-tabs capv2-views" role="tablist" aria-label="Episode">
            <button className="bhub-tab" aria-current={view === "witness"} onClick={() => setView("witness")}>Witness<span className="bhub-hint">the film</span></button>
            <button className="bhub-tab" aria-current={view === "conversation"} onClick={() => setView("conversation")}>Conversation<span className="bhub-hint">every step, inspectable</span></button>
            <button className="bhub-tab" aria-current={view === "trail"} onClick={() => setView("trail")}>Trail<span className="bhub-hint">what it touched</span></button>
          </div>
          {view === "witness" && <CastPlayer ev={evLike} />}
          {view === "conversation" && (
            trace
              ? <div className="capv2-trace-pane"><ConversationView steps={trace.steps} filters={allFilters} onOpenStep={() => {}} sliceRange={null} /></div>
              : <div className="v2-empty">Loading the sealed trajectory…</div>
          )}
          {view === "trail" && (
            trace
              ? <div className="capv2-trace-pane trail"><TrailView steps={trace.steps} classes={classes} focusedIdx={fi} onSelect={setFi} onOpenStep={() => {}} contextWaste={window.CONTEXT_WASTE} runHealth={window.RUN_HEALTH} wasteFilter={false} sliceRange={null} slices={null} slicerKey="full" activeSlice={null} onPickSlice={() => {}} /></div>
              : <div className="v2-empty">Loading the sealed trajectory…</div>
          )}
          <div className="ev-card">
            <div className="ev-card-head">Intent — what the agent was trying to do</div>
            <div className="capv2-intent">{cap.intent}</div>
            <div className="capv2-fail mono">{cap.fail.error}</div>
          </div>
        </div>
        <div className="ev-d-side">
          <ReenactComposer cap={cap} deps={x.deps} />
          <div className="ev-card ev-frozen">
            <div className="ev-card-head">Seal manifest</div>
            <div className="ev-frozen-rows mono">
              <div><span>envelope</span><span>{cap.v}</span></div>
              <div><span>trajectory</span><span>{cap.manifest.trajectory.steps} steps · {cap.manifest.trajectory.bytes}</span></div>
              <div><span>ctx</span><span>{cap.manifest.ctx.nodes} nodes · {cap.manifest.ctx.tokens}</span></div>
              <div><span>git</span><span>{cap.git.sha} · {cap.manifest.git.bytes}</span></div>
              <div><span>clearance</span><span>cleared (scanned {cap.scrub.scanned})</span></div>
            </div>
            <div className="ev-frozen-note">Replay classes: evidence bundle → <b>trajectory ships today</b> → actor → environment.</div>
          </div>
          <div className="ev-card">
            <div className="ev-card-head">Sealed replay ledger</div>
            {cap.replays.map((r, i) => (
              <div key={i} className="capv2-replay-row">
                <VerdictPill v={r.outcome} />
                <span className="capv2-replay-note">{r.note}</span>
                <span className="mono dim">@{r.sha} · {r.when}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

window.CapsulesV2Page = CapsulesV2Page;
window.CapsuleV2Detail = CapsuleV2Detail;
