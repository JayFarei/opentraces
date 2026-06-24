// RepoSettingsPage — answers: "How does opentraces behave in this project?"
// Every control maps to a real CLI surface:
//   tracking mode      → `opentraces config tracking-mode` / excluded marker
//   review policy      → `opentraces init --review-policy review|auto`
//   harness hooks      → `opentraces setup` / `init --agent`
//   ctx-tree capture   → optional OTLP capture (`ctx` commands)
//   scanning/redaction → `config set --classifier-sensitivity`, `--redact`
//   remote/visibility  → `opentraces remote set`, `push --private|--public|--gated`
//   detach             → `opentraces remove`

/* ── Small controls ──────────────────────────────────────────── */
function RsSwitch({ on, onChange, disabled }) {
  return (
    <button
      className="rs-switch"
      role="switch"
      aria-checked={on}
      data-on={on ? "1" : "0"}
      disabled={disabled}
      onClick={() => onChange && onChange(!on)}
    >
      <span className="knob" />
    </button>
  );
}

function RsRow({ title, desc, children, top }) {
  return (
    <div className="rs-row" data-top={top ? "1" : "0"}>
      <div className="rs-row-text">
        <div className="t">{title}</div>
        {desc && <div className="d">{desc}</div>}
      </div>
      <div className="rs-row-ctrl">{children}</div>
    </div>
  );
}

function RsSection({ id, icon, title, desc, children, tone }) {
  return (
    <section className="rs-section" id={id} data-tone={tone || "default"}>
      <header className="rs-sec-head">
        <span className="rs-sec-ico"><Icon name={icon} size={15} /></span>
        <div>
          <h3 className="rs-sec-title">{title}</h3>
          {desc && <p className="rs-sec-desc">{desc}</p>}
        </div>
      </header>
      <div className="rs-sec-body">{children}</div>
    </section>
  );
}

// Faint one-line CLI equivalent under a section's controls.
function RsCli({ cmd }) {
  return <div className="rs-cli mono">$ {cmd}</div>;
}

/* ── Tracking mode radio cards ───────────────────────────────── */
const RS_MODES = [
  { id: "global",   icon: "eye",     title: "Global tracking", desc: "Every agent session in this repo is captured automatically — private, review-required." },
  { id: "manual",   icon: "play",    title: "Manual",          desc: "Capture only sessions you start explicitly." },
  { id: "excluded", icon: "eye-off", title: "Excluded",        desc: "Never capture here, even with hooks installed." },
];

function RsModePicker({ value, onChange }) {
  return (
    <div className="rs-modes">
      {RS_MODES.map(m => (
        <button
          key={m.id}
          className="rs-mode"
          aria-current={value === m.id}
          onClick={() => onChange(m.id)}
        >
          <span className="rs-mode-ico"><Icon name={m.icon} size={14} /></span>
          <span className="rs-mode-t">{m.title}</span>
          <span className="rs-mode-d">{m.desc}</span>
          <span className="rs-mode-radio" data-on={value === m.id ? "1" : "0"} />
        </button>
      ))}
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────── */
function RepoSettingsPage({ repoId }) {
  const def = REPO_DEFS[repoId];
  if (!def) return null;
  const initial = React.useMemo(() => getRepoSettings(repoId), [repoId]);

  const [tracking, setTracking] = React.useState(initial.tracking);
  const [reviewPolicy, setReviewPolicy] = React.useState(initial.review_policy);
  const [visibility, setVisibility] = React.useState(initial.visibility);
  const [ctxCapture, setCtxCapture] = React.useState(initial.ctx_capture);
  const [sensitivity, setSensitivity] = React.useState(initial.scrub.sensitivity);
  const [redacts, setRedacts] = React.useState(initial.scrub.redact_strings);

  const workflows = (def.workflows || []).map(id => ({ id, ...WORKFLOW_DEFS[id] })).filter(w => w.name);

  return (
    <div className="landing landing-repo rs-page">
      <header className="repo-hero">
        <div className="rh-icon"><Icon name="settings" size={18} /></div>
        <div className="rh-title">
          <h1 className="rh-name"><span className="ns">{def.ns}/{def.nm} ·</span><span className="nm"> Settings</span></h1>
          <p className="rh-desc">How sessions are captured, scrubbed and published for this project.</p>
        </div>
        <div className="rh-meta">
          <div className="rh-stat"><div className="k">Tracking</div><div className="v mono">{tracking}</div></div>
          <div className="rh-stat"><div className="k">Review</div><div className="v mono">{reviewPolicy}</div></div>
          <div className="rh-stat"><div className="k">Bucket</div><div className="v mono">{initial.bucket_stats.size_gb} GB</div></div>
        </div>
      </header>

      <div className="rs-wrap">
        {/* General */}
        <RsSection id="rs-general" icon="repo" title="General" desc="Where this project lives and whether it is tracked.">
          <RsRow title="Local path" desc="Where the capture hooks watch for agent sessions.">
            <code className="rs-code mono">{initial.path}</code>
          </RsRow>
          <RsRow title="Project marker" desc="Committed to the repo so teammates capture with the same setup.">
            <code className="rs-code mono">{initial.marker}</code>
          </RsRow>
          <RsRow title="Bucket" desc={`Captured traces stay machine-local · ${def.total_traces} sessions · ${initial.bucket_stats.size_gb} GB`}>
            <code className="rs-code mono">{initial.bucket}</code>
          </RsRow>
          <RsRow title="Tracking mode" desc="Applies to all harnesses below." top>
            <RsModePicker value={tracking} onChange={setTracking} />
          </RsRow>
          <RsCli cmd="opentraces config tracking-mode manual" />
        </RsSection>

        {/* Capture */}
        <RsSection id="rs-capture" icon="activity" title="Capture" desc="Which agent harnesses have the capture hook installed.">
          <div className="rs-harnesses">
            {initial.harnesses.map(h => (
              <div key={h.id} className="rs-harness" data-installed={h.installed ? "1" : "0"}>
                <AgentGlyph agent={{ icon: h.icon }} size={15} />
                <div className="rs-h-text">
                  <div className="t">{h.name}</div>
                  <div className="d mono">
                    {h.installed ? `hook v${h.version} · last capture ${h.last}` : "hook not installed"}
                  </div>
                </div>
                {h.installed
                  ? <span className="rs-pill ok"><Icon name="check" size={11} />installed</span>
                  : <button className="tool-btn"><Icon name="plus" size={11} /><span>Install hook</span></button>}
              </div>
            ))}
          </div>
          <div className="rs-divider" />
          <RsRow title="Context-tree capture" desc="Also record what the agent saw (OTLP). Heavier traces; enables `ctx tree`.">
            <RsSwitch on={ctxCapture} onChange={setCtxCapture} />
          </RsRow>
          <RsCli cmd="opentraces setup" />
        </RsSection>

        {/* Scanning & redaction */}
        <RsSection id="rs-scrub" icon="shield" title="Scanning & redaction" desc={`Traces are scrubbed locally before anything is published. Last scan ${initial.scrub.last_scan}.`}>
          <RsRow title="Classifier sensitivity" desc="How aggressively the scanner flags potential secrets and personal data.">
            <div className="hm-toggle">
              {["low", "medium", "high"].map(s => (
                <button key={s} className={"hmt " + (sensitivity === s ? "on" : "")} onClick={() => setSensitivity(s)}>{s}</button>
              ))}
            </div>
          </RsRow>
          <RsRow title="Custom redaction strings" desc="Literal strings masked in every trace before review or publish." top>
            <div className="rs-redacts">
              {redacts.map(r => (
                <span key={r} className="rs-redact mono">
                  {r}
                  <button className="x" aria-label={`Remove ${r}`} onClick={() => setRedacts(rs => rs.filter(x => x !== r))}>
                    <Icon name="x" size={9} />
                  </button>
                </span>
              ))}
              {redacts.length === 0 && <span className="rail-empty">No custom strings — built-in detectors still apply.</span>}
              <button className="tool-btn"><Icon name="plus" size={11} /><span>Add string</span></button>
            </div>
          </RsRow>
          <RsCli cmd={'opentraces config set --redact "INTERNAL_API_KEY"'} />
        </RsSection>

        {/* Dataset workflows */}
        <RsSection id="rs-workflows" icon="workflows" title="Dataset workflows" desc="Row builders that project this project's traces into datasets.">
          {workflows.length === 0 && <div className="rail-empty">No workflows attached — traces stay in the bucket until you add one.</div>}
          {workflows.map(w => (
            <div key={w.id} className="rs-workflow">
              <span className="rs-route-ico"><Icon name="workflows" size={13} /></span>
              <div className="rs-route-text">
                <div className="t">
                  <span className="mono nm">{w.name}</span>
                  {w.producedDatasets && w.producedDatasets.length > 0 && (
                    <React.Fragment>
                      <span className="arr">→</span>
                      <span className="then mono">{w.producedDatasets.map(d => "@" + d).join(", ")}</span>
                    </React.Fragment>
                  )}
                </div>
                <div className="d">{w.schedule} · last run {w.lastFired}</div>
              </div>
              <button className="tool-btn"><Icon name="play" size={11} /><span>Run</span></button>
            </div>
          ))}
          <RsCli cmd="opentraces dataset run shortcuts-bench" />
        </RsSection>

        {/* Publishing */}
        <RsSection id="rs-publish" icon="send" title="Publishing" desc="Nothing leaves this machine until rows are reviewed and pushed.">
          <div className="rs-remote">
            <span className="rs-remote-ico hf"><HuggingFaceLogo size={14} /></span>
            <div className="rs-h-text">
              <div className="t">Dataset remote</div>
              <div className="d mono">{initial.remote.repo} · Hugging Face Hub</div>
            </div>
            <span className="rs-pill ok"><Icon name="check" size={11} />connected</span>
          </div>
          <RsRow title="Review policy" desc="`review` queues rows for approval; `auto` publishes once the scan passes.">
            <div className="hm-toggle">
              <button className={"hmt " + (reviewPolicy === "review" ? "on" : "")} onClick={() => setReviewPolicy("review")}>
                <Icon name="eye" size={11} /> Review
              </button>
              <button className={"hmt " + (reviewPolicy === "auto" ? "on" : "")} onClick={() => setReviewPolicy("auto")}>
                <Icon name="check" size={11} /> Auto
              </button>
            </div>
          </RsRow>
          <RsRow title="Visibility" desc="Applied on push. Gated datasets require access requests on the Hub.">
            <div className="hm-toggle">
              <button className={"hmt " + (visibility === "private" ? "on" : "")} onClick={() => setVisibility("private")}>
                <Icon name="lock" size={11} /> Private
              </button>
              <button className={"hmt " + (visibility === "public" ? "on" : "")} onClick={() => setVisibility("public")}>
                <Icon name="globe" size={11} /> Public
              </button>
              <button className={"hmt " + (visibility === "gated" ? "on" : "")} onClick={() => setVisibility("gated")}>
                <Icon name="unlock" size={11} /> Gated
              </button>
            </div>
          </RsRow>
          <RsCli cmd="opentraces remote set jayfarei/opentraces" />
        </RsSection>

        {/* Danger zone */}
        <RsSection id="rs-danger" icon="alert" title="Danger zone" tone="danger">
          <RsRow title="Exclude this project" desc="Writes an excluded marker — capture stops, retained traces stay in the bucket.">
            <button className="rs-danger-btn" onClick={() => setTracking("excluded")} disabled={tracking === "excluded"}>
              {tracking === "excluded" ? "Excluded" : "Exclude"}
            </button>
          </RsRow>
          <RsRow title="Detach project" desc="Removes hooks and the local inbox (`opentraces remove`). Bucket evidence is kept.">
            <button className="rs-danger-btn">Detach</button>
          </RsRow>
        </RsSection>
      </div>
    </div>
  );
}

window.RepoSettingsPage = RepoSettingsPage;
