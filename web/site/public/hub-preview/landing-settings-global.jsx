// GlobalSettingsPage — answers: "What is my OpenTraces setup, across all my
// machines — and what's allowed to leave them?"
//
// Everything maps to the real surface:
//   identity        → `opentraces auth login` (HF OAuth device flow), whoami
//   bucket remote   → bucket.remote config: provider huggingface, private-only,
//                     sync_policy daemon|manual; `bucket remote status/push/pull`
//   machines        → derived from manifest pushes into the bucket remote
//                     (digest per machine; no central registry)
//   local bucket    → `bucket status` counters; verify / repair / prune verbs
//   security tools  → security.* registry: regex, entropy, trufflehog,
//                     privacy_filter, business_logic, path_anonymizer, classifier;
//                     named policies off|basic|recommended|strict (machine-wide)
//   redact strings  → custom_redact_strings (global)
//   defaults        → dataset_visibility; review/push policy live per-project
//   projects        → project registry (project_id ↔ slug), excluded_projects

/* ── Mock state ──────────────────────────────────────────────── */
const GS_ACCOUNT = {
  username: "jayfarei",
  fullName: "Jay Farei",
  authMethod: "Hugging Face OAuth · device flow",
  scopes: ["openid", "profile", "write-repos", "manage-repos"],
  credentialsPath: "~/.opentraces/credentials",
  tokenAge: "authorized 3 weeks ago",
};

const GS_REMOTE = {
  enabled: true,
  repo: "jayfarei/opentraces-bucket",
  provider: "Hugging Face Hub",
  visibility: "private",
  localDigest: "9f3c2ab",
  remoteDigest: "9f3c2ab",
  state: "in-sync",
};

const GS_MACHINES = [
  {
    id: "jay-mbp-m4", name: "jay-mbp-m4", os: "macOS 15.3", thisMachine: true,
    state: "in-sync", policy: "daemon", lastPush: "12 min ago",
    digest: "9f3c2ab", traces: 47, sizeGb: 1.2, hooks: 3, version: "0.4.2",
    blocked: [],
  },
  {
    id: "jay-studio", name: "jay-studio", os: "macOS 15.2", thisMachine: false,
    state: "ahead", policy: "daemon", lastPush: "6 h ago",
    digest: "b81d04e", traces: 31, sizeGb: 0.8, hooks: 2, version: "0.4.2",
    blocked: ["3 traces pending security scan"],
  },
  {
    id: "ci-runner-04", name: "ci-runner-04", os: "Ubuntu 24.04 · headless", thisMachine: false,
    state: "behind", policy: "manual", lastPush: "3 d ago",
    digest: "77a91cf", traces: 12, sizeGb: 0.3, hooks: 1, version: "0.3.9",
    blocked: [],
  },
];

const GS_BUCKET_STATS = [
  { k: "Traces", v: "47" },
  { k: "Raw sources", v: "112" },
  { k: "Trail events", v: "1,408" },
  { k: "Syncable", v: "44" },
  { k: "Stale security", v: "3" },
  { k: "Unfiltered", v: "0" },
  { k: "Last ingest", v: "12 min ago" },
];

// Security tool registry — names and grouping match the CLI.
const GS_TOOLS = [
  { id: "regex",           name: "regex",           desc: "Built-in regex secret detector." },
  { id: "entropy",         name: "entropy",         desc: "High-entropy string detector." },
  { id: "business_logic",  name: "business_logic",  desc: "Internal infrastructure & business-signal detector." },
  { id: "path_anonymizer", name: "path_anonymizer", desc: "Rewrites local usernames in file paths." },
  { id: "classifier",      name: "classifier",      desc: "Heuristic content classifier.", extra: "sensitivity" },
  { id: "trufflehog",      name: "trufflehog",      desc: "TruffleHog secret scan with optional live verification.", extra: "verify" },
  { id: "privacy_filter",  name: "privacy_filter",  desc: "openai/privacy-filter model · score threshold 0.7." },
];

const GS_POLICIES = {
  off:         [],
  basic:       ["regex", "entropy"],
  recommended: ["regex", "entropy", "business_logic", "path_anonymizer", "classifier"],
  strict:      ["regex", "entropy", "trufflehog", "privacy_filter", "business_logic", "path_anonymizer", "classifier"],
};

const GS_STATE_META = {
  "in-sync":  { label: "in sync",  color: "var(--c-git)" },
  "ahead":    { label: "ahead",    color: "var(--c-user)" },
  "behind":   { label: "behind",   color: "var(--c-plan)" },
  "diverged": { label: "diverged", color: "var(--c-error)" },
};

/* ── Machine row ─────────────────────────────────────────────── */
function GsMachine({ m }) {
  const meta = GS_STATE_META[m.state];
  return (
    <div className="gs-machine" data-this={m.thisMachine ? "1" : "0"}>
      <span className="gs-m-ico"><Icon name={m.thisMachine ? "monitor" : "server"} size={15} /></span>
      <div className="gs-m-id">
        <div className="t">
          <span className="mono nm">{m.name}</span>
          {m.thisMachine && <span className="gs-this">this machine</span>}
        </div>
        <div className="d">{m.os} · cli v{m.version} · {m.hooks} harness {m.hooks === 1 ? "hook" : "hooks"}</div>
      </div>
      <div className="gs-m-stat">
        <div className="k">Traces</div>
        <div className="v mono">{m.traces} · {m.sizeGb} GB</div>
      </div>
      <div className="gs-m-stat">
        <div className="k">Sync</div>
        <div className="v mono">{m.policy}</div>
      </div>
      <div className="gs-m-stat">
        <div className="k">Last push</div>
        <div className="v">{m.lastPush}</div>
      </div>
      <div className="gs-m-state">
        <span className="gs-chip" style={{ "--chip": meta.color }}>{meta.label}</span>
        <span className="mono dg">{m.digest}</span>
      </div>
      {m.blocked.length > 0 && (
        <div className="gs-m-blocked">
          <Icon name="alert" size={11} />
          <span>{m.blocked.join(" · ")}</span>
        </div>
      )}
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────── */
function GlobalSettingsPage({ onSelectRepo }) {
  const [syncPolicy, setSyncPolicy] = React.useState("daemon");
  const [policy, setPolicy] = React.useState("recommended");
  const [tools, setTools] = React.useState(new Set(GS_POLICIES.recommended));
  const [sensitivity, setSensitivity] = React.useState("medium");
  const [verifySecrets, setVerifySecrets] = React.useState(false);
  const [redacts, setRedacts] = React.useState(["INTERNAL_API_KEY", "ACME_STAGING_URL", "jay@acme-internal.io"]);
  const [dsVisibility, setDsVisibility] = React.useState("private");

  const applyPolicy = (p) => {
    setPolicy(p);
    setTools(new Set(GS_POLICIES[p]));
  };
  const toggleTool = (id) => {
    setTools(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      // does the new set exactly match a named policy?
      const match = Object.entries(GS_POLICIES).find(([, t]) =>
        t.length === next.size && t.every(x => next.has(x)));
      setPolicy(match ? match[0] : "custom");
      return next;
    });
  };

  const projects = Object.entries(REPO_DEFS);

  return (
    <div className="landing landing-repo gs-page">
      <header className="repo-hero gs-hero">
        <img className="gs-avatar" src="assets/avatar-jf.png" alt="" width="44" height="44" />
        <div className="rh-title">
          <h1 className="rh-name"><span className="nm">Settings</span></h1>
          <p className="rh-desc">
            Signed in as <strong>{GS_ACCOUNT.username}</strong> · {GS_ACCOUNT.authMethod}
          </p>
        </div>
        <div className="rh-meta">
          <div className="rh-stat"><div className="k">Machines</div><div className="v mono">{GS_MACHINES.length}</div></div>
          <div className="rh-stat"><div className="k">Projects</div><div className="v mono">{projects.length}</div></div>
          <div className="rh-stat"><div className="k">Bucket</div><div className="v mono">{GS_REMOTE.state}</div></div>
        </div>
      </header>

      <div className="rs-wrap">
        {/* Account */}
        <RsSection id="gs-account" icon="user" title="Account" desc="OpenTraces authenticates through your Hugging Face account — datasets, remotes and the bucket all live under it.">
          <RsRow title="Identity" desc={GS_ACCOUNT.tokenAge}>
            <div className="gs-identity">
              <img src="assets/avatar-jf.png" alt="" width="22" height="22" />
              <span className="mono">{GS_ACCOUNT.username}</span>
            </div>
          </RsRow>
          <RsRow title="Token scopes" desc={`Stored at ${GS_ACCOUNT.credentialsPath} (0600).`}>
            <div className="gs-scopes">
              {GS_ACCOUNT.scopes.map(s => <span key={s} className="gs-scope mono">{s}</span>)}
            </div>
          </RsRow>
          <RsRow title="Session" desc="Re-authenticate with a different token, or remove credentials from this machine.">
            <div className="gs-btn-row">
              <button className="tool-btn"><Icon name="refresh" size={11} /><span>Re-authenticate</span></button>
              <button className="tool-btn"><Icon name="x" size={11} /><span>Log out</span></button>
            </div>
          </RsRow>
          <RsCli cmd="opentraces auth login" />
        </RsSection>

        {/* Bucket remote & machines */}
        <RsSection id="gs-machines" icon="git-branch" title="Bucket remote & machines" desc="Every machine mirrors its local bucket into one private remote. Machines are inferred from manifest pushes — there is no central registry.">
          <div className="gs-remote-line">
            <span className="rs-remote-ico hf"><HuggingFaceLogo size={14} /></span>
            <div className="rs-h-text">
              <div className="t mono">{GS_REMOTE.repo}</div>
              <div className="d">{GS_REMOTE.provider} · always private</div>
            </div>
            <div className="gs-digests mono">
              <span title="local digest">local {GS_REMOTE.localDigest}</span>
              <span className="eq" data-match={GS_REMOTE.localDigest === GS_REMOTE.remoteDigest ? "1" : "0"}>
                {GS_REMOTE.localDigest === GS_REMOTE.remoteDigest ? "=" : "≠"}
              </span>
              <span title="remote digest">remote {GS_REMOTE.remoteDigest}</span>
            </div>
            <span className="gs-chip" style={{ "--chip": GS_STATE_META[GS_REMOTE.state].color }}>{GS_STATE_META[GS_REMOTE.state].label}</span>
          </div>

          <RsRow title="Sync policy" desc="The daemon pushes after every eligible capture; manual syncs only on `bucket remote push`.">
            <div className="hm-toggle">
              <button className={"hmt " + (syncPolicy === "daemon" ? "on" : "")} onClick={() => setSyncPolicy("daemon")}>Daemon</button>
              <button className={"hmt " + (syncPolicy === "manual" ? "on" : "")} onClick={() => setSyncPolicy("manual")}>Manual</button>
            </div>
          </RsRow>

          <div className="rs-subhead"><span>Machines publishing into this bucket</span></div>
          <div className="gs-machines">
            {GS_MACHINES.map(m => <GsMachine key={m.id} m={m} />)}
          </div>
          <RsCli cmd="opentraces bucket remote status" />
        </RsSection>

        {/* Local bucket health */}
        <RsSection id="gs-bucket" icon="box" title="Local bucket health" desc="This machine's bucket at ~/.opentraces/ — counters from the last manifest write.">
          <div className="gs-stats">
            {GS_BUCKET_STATS.map(s => (
              <div key={s.k} className="gs-stat" data-warn={s.k === "Stale security" && s.v !== "0" ? "1" : "0"}>
                <div className="k">{s.k}</div>
                <div className="v mono">{s.v}</div>
              </div>
            ))}
          </div>
          <RsRow title="Maintenance" desc="Verify blob integrity, re-project from the event log, or prune orphan blobs. All idempotent; verify and prune --dry-run are read-only.">
            <div className="gs-btn-row">
              <button className="tool-btn"><Icon name="check" size={11} /><span>Verify</span></button>
              <button className="tool-btn"><Icon name="refresh" size={11} /><span>Repair</span></button>
              <button className="tool-btn"><Icon name="trash" size={11} /><span>Prune…</span></button>
            </div>
          </RsRow>
          <RsCli cmd="opentraces bucket verify --sample 100" />
        </RsSection>

        {/* Security & scrubbing */}
        <RsSection id="gs-security" icon="shield" title="Security & scrubbing" desc="Machine-wide pipeline shared by capture-time sanitization and bucket sync. Nothing syncs until it passes the enabled tools.">
          <RsRow title="Policy" desc="Named presets set the exact tool set; flipping any tool below makes the policy custom." top>
            <div className="hm-toggle gs-policy">
              {Object.keys(GS_POLICIES).map(p => (
                <button key={p} className={"hmt " + (policy === p ? "on" : "")} onClick={() => applyPolicy(p)}>{p}</button>
              ))}
              {policy === "custom" && <button className="hmt on">custom</button>}
            </div>
          </RsRow>

          <div className="gs-tools">
            {GS_TOOLS.map(t => (
              <div key={t.id} className="gs-tool" data-on={tools.has(t.id) ? "1" : "0"}>
                <div className="rs-h-text">
                  <div className="t mono">{t.name}</div>
                  <div className="d">{t.desc}</div>
                </div>
                {t.extra === "sensitivity" && tools.has(t.id) && (
                  <div className="hm-toggle sm">
                    {["low", "medium", "high"].map(s => (
                      <button key={s} className={"hmt " + (sensitivity === s ? "on" : "")} onClick={() => setSensitivity(s)}>{s}</button>
                    ))}
                  </div>
                )}
                {t.extra === "verify" && tools.has(t.id) && (
                  <label className="gs-sub-toggle">
                    <span>verify secrets</span>
                    <RsSwitch on={verifySecrets} onChange={setVerifySecrets} />
                  </label>
                )}
                <RsSwitch on={tools.has(t.id)} onChange={() => toggleTool(t.id)} />
              </div>
            ))}
          </div>

          <RsRow title="Global redaction strings" desc="Masked in every trace on every project, before review or publish." top>
            <div className="rs-redacts">
              {redacts.map(r => (
                <span key={r} className="rs-redact mono">
                  {r}
                  <button className="x" aria-label={`Remove ${r}`} onClick={() => setRedacts(rs => rs.filter(x => x !== r))}>
                    <Icon name="x" size={9} />
                  </button>
                </span>
              ))}
              <button className="tool-btn"><Icon name="plus" size={11} /><span>Add string</span></button>
            </div>
          </RsRow>
          <RsCli cmd="opentraces bucket security --policy recommended" />
        </RsSection>

        {/* Publishing defaults */}
        <RsSection id="gs-defaults" icon="send" title="Publishing defaults" desc="Account-wide defaults; each project's marker can override them.">
          <RsRow title="New dataset visibility" desc="Applied when a dataset remote is created during init.">
            <div className="hm-toggle">
              <button className={"hmt " + (dsVisibility === "private" ? "on" : "")} onClick={() => setDsVisibility("private")}>
                <Icon name="lock" size={11} /> Private
              </button>
              <button className={"hmt " + (dsVisibility === "public" ? "on" : "")} onClick={() => setDsVisibility("public")}>
                <Icon name="globe" size={11} /> Public
              </button>
            </div>
          </RsRow>
          <RsRow title="Review & push policies" desc="These live in each project's committed marker, not here.">
            <span className="gs-hint">Configure per project → Settings</span>
          </RsRow>
          <RsCli cmd="opentraces config set --dataset-visibility private" />
        </RsSection>

        {/* Registered projects */}
        <RsSection id="gs-projects" icon="repo" title="Registered projects" desc="The project registry links each repo's committed marker to a local slug. Cloning a repo on a new machine and running `init` re-registers the same id.">
          <div className="gs-projects">
            {projects.map(([id, def]) => (
              <button key={id} className="gs-project" onClick={() => onSelectRepo(id)}>
                <Icon name="git-branch" size={13} />
                <span className="repo-label"><span className="ns">{def.ns}/</span><span className="nm">{def.nm}</span></span>
                <span className="mono slug">{def.nm}-{String(Math.abs(id.split("").reduce((a, c) => a * 31 + c.charCodeAt(0) | 0, 7))).slice(0, 4)}</span>
                <span className="mono ct">{def.total_traces} traces</span>
                <Icon name="chevron-right" size={11} className="arr" />
              </button>
            ))}
          </div>
          <RsCli cmd="opentraces init" />
        </RsSection>
      </div>
    </div>
  );
}

window.GlobalSettingsPage = GlobalSettingsPage;
