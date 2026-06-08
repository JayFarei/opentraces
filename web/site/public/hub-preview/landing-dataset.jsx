// DatasetLanding — overview / inbox / workflow tabs.
//
// Mental model:
//   - A dataset is local-first. Items are produced by its workflow into the INBOX.
//   - The user reviews items in the inbox and promotes them to STAGING.
//   - Push sends staged items to the remote. (Auto-policy: skip review, auto-stage on produce.)
//   - Three remote actions: Add remote · Pull · Push.

function PipelineDiagram({ ds, onOpenInbox }) {
  const hasRemote = !!ds.remote_url;
  return (
    <div className="pipeline">
      <button className="pipe-node inbox" onClick={onOpenInbox}>
        <div className="pn-icon"><Icon name="inbox" size={14} /></div>
        <div className="pn-label">Inbox</div>
        <div className="pn-count mono">{ds.inbox_count}</div>
        <div className="pn-sub">{ds.auto_policy ? "auto-staged" : "needs review"}</div>
      </button>

      <div className="pipe-arrow">
        <div className="pa-line" />
        <div className="pa-cap">
          {ds.auto_policy ? <span className="pa-label auto">auto</span> : <span className="pa-label">promote</span>}
        </div>
      </div>

      <div className="pipe-node staging">
        <div className="pn-icon"><Icon name="git-commit" size={14} /></div>
        <div className="pn-label">Staging</div>
        <div className="pn-count mono">{ds.staging_count}</div>
        <div className="pn-sub">ready to push</div>
      </div>

      <div className="pipe-arrow">
        <div className="pa-line" />
        <div className="pa-cap">
          <span className="pa-label">push</span>
        </div>
      </div>

      <div className={"pipe-node remote " + (hasRemote ? "" : "absent")}>
        <div className="pn-icon"><Icon name={hasRemote ? "up-line" : "plus"} size={14} /></div>
        <div className="pn-label">{hasRemote ? "Remote" : "No remote"}</div>
        <div className="pn-count mono">{hasRemote ? ds.remote_commits : "—"}</div>
        <div className="pn-sub">
          {hasRemote ? <span className={"remote-tag remote-" + ds.remote}>{ds.remote}</span> : "add one to push"}
        </div>
      </div>
    </div>
  );
}

function RemoteActions({ ds }) {
  const hasRemote = !!ds.remote_url;
  return (
    <div className="remote-actions">
      {!hasRemote && (
        <button className="ra-btn primary"><Icon name="plus" size={12} />Add remote</button>
      )}
      {hasRemote && (
        <>
          <button className="ra-btn"><Icon name="down-line" size={12} />Pull</button>
          <button className="ra-btn primary" disabled={ds.staging_count === 0}>
            <Icon name="up-line" size={12} />Push {ds.staging_count > 0 && <span className="ra-count">{ds.staging_count}</span>}
          </button>
        </>
      )}
    </div>
  );
}

function CatchUpBanner({ ds, onOpenInbox }) {
  if (ds.inbox_count === 0) return null;
  return (
    <button className="catchup" onClick={onOpenInbox}>
      <div className="cu-icon"><Icon name="inbox" size={16} /></div>
      <div className="cu-body">
        <div className="cu-title">Let me catch you up on this.</div>
        <div className="cu-sub">
          <span className="mono">{ds.inbox_count}</span> new {ds.inbox_count === 1 ? "item" : "items"} since you last reviewed.
          {ds.auto_policy ? " Auto-staging is on, so they'll be pushed on next run." : " Review and promote to staging when ready."}
        </div>
      </div>
      <div className="cu-cta">Open inbox →</div>
    </button>
  );
}

function DatasetTabs({ active, onChange, inboxCount }) {
  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "inbox",    label: "Inbox", count: inboxCount },
    { id: "workflow", label: "Workflow" },
  ];
  return (
    <div className="ds-tabs">
      {tabs.map(t => (
        <button
          key={t.id}
          className="ds-tab"
          aria-current={active === t.id}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {typeof t.count === "number" && t.count > 0 && <span className="ds-tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

function DatasetOverview({ ds, runs, okCount, onOpenInbox }) {
  return (
    <>
      <CatchUpBanner ds={ds} onOpenInbox={onOpenInbox} />

      <PipelineDiagram ds={ds} onOpenInbox={onOpenInbox} />

      <div className="ds-strip" style={{ marginTop: 18 }}>
        <div className="ds-strip-item">
          <div className="k">Rows (local)</div>
          <div className="v mono">{ds.rows.toLocaleString()}</div>
        </div>
        <div className="ds-strip-item">
          <div className="k">Columns</div>
          <div className="v mono">{ds.cols}</div>
        </div>
        <div className="ds-strip-item">
          <div className="k">Last run</div>
          <div className="v">{ds.last_run}</div>
        </div>
        <div className="ds-strip-item">
          <div className="k">Success</div>
          <div className="v mono">{Math.round((okCount / runs.length) * 100)}%</div>
        </div>
        <div className="ds-strip-item">
          <div className="k">Push policy</div>
          <div className="v">{ds.auto_policy ? <span className="policy-tag auto">auto</span> : <span className="policy-tag manual">manual review</span>}</div>
        </div>
      </div>

      <div className="ds-grid" style={{ marginTop: 18 }}>
        <section className="ds-col-main">
          <h3 className="repo-h3">Sample rows</h3>
          <div className="ds-sample-wrap">
            <table className="ds-sample">
              <thead>
                <tr>{ds.sample_columns.map(c => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {ds.sample_rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, ci) => (
                      <td key={ci} className="mono">{typeof cell === "string" && cell.length > 40 ? cell.slice(0, 40) + "…" : String(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="ds-sample-foot">Showing 4 of {ds.rows.toLocaleString()} rows · <button className="link">view all</button></div>
          </div>

          <h3 className="repo-h3" style={{ marginTop: 24 }}>Run history</h3>
          <div className="ds-run-chart">
            {runs.slice().reverse().map((r, i) => (
              <div
                key={i}
                className={"ds-run-bar " + r.status}
                style={{ height: 8 + (r.rows / 12000) * 60 }}
                title={`${relTime(r.timestamp)} · ${r.rows.toLocaleString()} rows · ${r.status}`}
              />
            ))}
          </div>
          <div className="ds-run-axis">
            <span className="mono">{relTime(runs[runs.length-1].timestamp)}</span>
            <span className="mono">now</span>
          </div>
        </section>

        <aside className="ds-col-side">
          <div className="rail-card">
            <div className="rail-h">
              {ds.remote_url ? (
                <span className="rail-h-hf">
                  <HuggingFaceLogo size={12} />
                  <span>Hugging Face</span>
                </span>
              ) : "Remote"}
            </div>
            {ds.remote_url ? (
              <>
                <a
                  className="rail-hf-link mono"
                  href={ds.remote_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={`Open ${ds.remote_repo} on Hugging Face`}
                >
                  {ds.remote_repo}
                  <Icon name="external" size={11} />
                </a>
                <div className="rail-row">
                  <span>Commits</span>
                  <span className="mono rail-meta">local {ds.local_commits} · remote {ds.remote_commits}</span>
                </div>
                <div className="rail-row">
                  <span>Status</span>
                  <span className={"remote-tag remote-" + ds.remote}>{ds.remote}</span>
                </div>
                {ds.local_commits - ds.remote_commits > 0 && (
                  <div className="rail-diff ahead">
                    <Icon name="up-line" size={11} />
                    <span className="mono">{ds.local_commits - ds.remote_commits}</span>
                    <span>commit{ds.local_commits - ds.remote_commits === 1 ? "" : "s"} waiting to push</span>
                  </div>
                )}
                {ds.remote_commits - ds.local_commits > 0 && (
                  <div className="rail-diff behind">
                    <Icon name="down-line" size={11} />
                    <span className="mono">{ds.remote_commits - ds.local_commits}</span>
                    <span>commit{ds.remote_commits - ds.local_commits === 1 ? "" : "s"} to pull</span>
                  </div>
                )}
              </>
            ) : (
              <div className="rail-empty">Local-only. Add a Hugging Face remote to share or push.</div>
            )}
          </div>
          <div className="rail-card">
            <div className="rail-h">Latest run</div>
            <button className="latest-run">
              <Fingerprint classes={runs[0].fingerprint} height={28} gap={1} />
              <div className="lr-meta">
                <span className="mono">{runs[0].id}</span>
                <span className="dot-sep" />
                <span>{relTime(runs[0].timestamp)}</span>
              </div>
              <div className="lr-status"><StatusPill status={runs[0].status} /></div>
            </button>
          </div>
        </aside>
      </div>
    </>
  );
}

function DatasetInbox({ ds }) {
  const baseItems = React.useMemo(() => buildInboxItems(ds.id || "", ds.inbox_count), [ds.id, ds.inbox_count]);
  const [items, setItems] = React.useState(() => baseItems);

  // Recompute when the dataset changes
  React.useEffect(() => { setItems(baseItems); }, [baseItems]);

  const stagedCount = items.filter(i => i.staged).length + (ds.staging_count || 0);
  const pendingCount = items.filter(i => !i.staged).length;

  const promote = (id) => setItems(arr => arr.map(it => it.id === id ? { ...it, staged: true } : it));
  const skip    = (id) => setItems(arr => arr.filter(it => it.id !== id));
  const promoteAll = () => setItems(arr => arr.map(it => ({ ...it, staged: true })));

  return (
    <div className="ds-inbox">
      <header className="inbox-hero">
        <div className="ih-title">
          <h2>Inbox</h2>
          <p className="ih-sub">
            New items from <span className="mono">{ds.workflow_label}</span>.
            {ds.auto_policy
              ? " Auto-policy is on — items go straight to staging."
              : " Review each item and promote to staging to make it a candidate for the next push."}
          </p>
        </div>
        <div className="ih-counts">
          <div className="ih-stat">
            <div className="k">Pending</div>
            <div className="v mono">{pendingCount}</div>
          </div>
          <div className="ih-stat">
            <div className="k">Staged</div>
            <div className="v mono">{stagedCount}</div>
          </div>
          <button className="ra-btn" onClick={promoteAll} disabled={pendingCount === 0}>
            Promote all
          </button>
        </div>
      </header>

      {items.length === 0 && (
        <div className="inbox-empty">
          <Icon name="inbox" size={20} />
          <div className="ie-title">Inbox zero</div>
          <div className="ie-sub">Nothing waiting. The next run will drop new items here.</div>
        </div>
      )}

      {items.length > 0 && (
        <div className="inbox-list">
          {items.map(item => (
            <div key={item.id} className={"inbox-item " + (item.staged ? "staged" : "")}>
              <div className="ii-id mono">{item.id}</div>
              <div className="ii-reason"><span className={"reason-tag r-" + item.reason}>{item.reason}</span></div>
              <div className="ii-preview">
                {item.preview.map((p, i) => <span key={i} className="ii-prev mono">{p}</span>)}
              </div>
              <div className="ii-conf">
                <div className="conf-bar"><div className="conf-fill" style={{ width: (item.confidence * 100) + "%" }} /></div>
                <div className="conf-num mono">{item.confidence.toFixed(2)}</div>
              </div>
              <div className="ii-when mono">{relTime(item.added_at)}</div>
              <div className="ii-actions">
                {item.staged ? (
                  <span className="ii-staged">staged ✓</span>
                ) : (
                  <>
                    <button className="ii-btn" onClick={() => skip(item.id)} title="Discard from inbox">Skip</button>
                    <button className="ii-btn primary" onClick={() => promote(item.id)} title="Move to staging">Promote</button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Security + privacy tools that can be attached to a workflow. Mirrors the real
// security tool registry (regex → entropy → trufflehog → privacy_filter →
// llm_pii → business_logic → path_anonymizer → capsule_scope → classifier).
const WF_SECURITY_TOOLS = [
  { name: "Secret regex",        desc: "Built-in patterns for API keys, tokens, and credentials.", on: true },
  { name: "Entropy scan",        desc: "Flags high-entropy strings that look like secrets.",        on: true },
  { name: "TruffleHog",          desc: "Verified secret detection across hundreds of providers.",    on: true },
  { name: "Path anonymizer",     desc: "Rewrites absolute home and project paths.",                  on: true },
  { name: "PII filter",          desc: "On-device NER redacts names, emails, and personal data.",    on: false },
  { name: "LLM PII review",      desc: "Second-pass model judgement on ambiguous PII.",              on: false },
  { name: "Business-logic redaction", desc: "Your own rules for proprietary strings and values.",    on: false },
  { name: "Sensitivity classifier", desc: "Tags each record so you gate what is allowed to publish.", on: false },
];

function DatasetWorkflow({ ds, runs, okCount, onSelectTrace }) {
  const wf = WORKFLOW_DEFS[ds.workflow];
  if (!wf) return <div style={{ padding: 24, color: "var(--fg-mute)" }}>No workflow attached.</div>;

  return (
    <div className="ds-workflow">
      <header className="dwf-hero">
        <div className="dwf-title">
          <div className="dwf-kicker">Workflow that produces this dataset</div>
          <h2 className="dwf-name">{wf.name}</h2>
          <p className="dwf-desc">{wf.description}</p>
        </div>
        <div className="dwf-actions">
          <button className="wf-run-btn"><Icon name="git-commit" size={12} />Run now</button>
          <button className="wf-edit-btn">Edit</button>
        </div>
      </header>

      <div className="wf-strip">
        <div className="wf-strip-item">
          <div className="k">Schedule</div>
          <div className="v">{wf.schedule}</div>
        </div>
        <div className="wf-strip-item">
          <div className="k">Last fired</div>
          <div className="v">{wf.lastFired}</div>
        </div>
        {wf.nextFire && (
          <div className="wf-strip-item">
            <div className="k">Next</div>
            <div className="v">{wf.nextFire}</div>
          </div>
        )}
        <div className="wf-strip-item">
          <div className="k">Triggers</div>
          <div className="v wf-triggers">
            {wf.triggers.map(tr => <span key={tr} className="wf-trigger">{tr}</span>)}
          </div>
        </div>
        <div className="wf-strip-item">
          <div className="k">Success</div>
          <div className="v mono">{Math.round((okCount / runs.length) * 100)}%</div>
        </div>
      </div>

      <div className="wf-sec-head">
        <h3 className="repo-h3" style={{ marginTop: 24, marginBottom: 0 }}>Security &amp; privacy</h3>
        <span className="wf-sec-count mono">
          {WF_SECURITY_TOOLS.filter(t => t.on).length}/{WF_SECURITY_TOOLS.length} on
        </span>
      </div>
      <p className="wf-sec-note">
        Every row this workflow produces is scanned and redacted before it lands. Turn tools on per workflow.
      </p>
      <div className="wf-sec-grid">
        {WF_SECURITY_TOOLS.map(t => (
          <div key={t.name} className="wf-sec-tool" data-on={t.on ? "1" : "0"}>
            <span className="wf-sec-dot" />
            <div className="wf-sec-main">
              <div className="wf-sec-name">{t.name}</div>
              <div className="wf-sec-desc">{t.desc}</div>
            </div>
            <span className="wf-sec-state">{t.on ? "on" : "available"}</span>
          </div>
        ))}
      </div>

      <h3 className="repo-h3" style={{ marginTop: 24 }}>Instructions</h3>
      <pre className="wf-instructions">{wf.instructions}</pre>

      <h3 className="repo-h3" style={{ marginTop: 24 }}>Recent runs</h3>
      <div className="run-list">
        {runs.map(r => (
          <button key={r.id} className="run-row" onClick={() => onSelectTrace(r.id)}>
            <span className={"run-dot " + r.status} />
            <span className="run-when mono">{relTime(r.timestamp)}</span>
            <Fingerprint classes={r.fingerprint} height={14} gap={1} className="run-fp" />
            <span className="run-id mono">{r.id}</span>
            <span className="run-rows mono">{r.rows.toLocaleString()} rows</span>
            <span className="run-dur mono">{formatShort(r.duration_s)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function DatasetLanding({ datasetId, child, onSelectTrace, onSelectChild }) {
  const ds = DATASET_DEFS[datasetId];
  if (!ds) return null;
  // expose id back for the inbox builder
  const dsWithId = { ...ds, id: datasetId };
  const runs = React.useMemo(() => buildWorkflowRuns(ds.workflow), [ds.workflow]);
  const okCount = runs.filter(r => r.status === "ok").length;
  const active = child || "overview";

  return (
    <div className="landing landing-dataset">
      <header className="ds-hero">
        <div className="ds-icon"><Icon name="datasets" size={18} /></div>
        <div className="ds-title">
          <div className="ds-kicker">Dataset</div>
          <h1 className="ds-name">{ds.name}</h1>
          <p className="ds-desc">{ds.description}</p>
        </div>
        <RemoteActions ds={dsWithId} />
      </header>

      <DatasetTabs active={active} onChange={onSelectChild} inboxCount={ds.inbox_count} />

      {active === "overview" && (
        <DatasetOverview
          ds={dsWithId}
          runs={runs}
          okCount={okCount}
          onOpenInbox={() => onSelectChild("inbox")}
        />
      )}
      {active === "inbox" && <DatasetInbox ds={dsWithId} />}
      {active === "workflow" && (
        <DatasetWorkflow ds={dsWithId} runs={runs} okCount={okCount} onSelectTrace={onSelectTrace} />
      )}
    </div>
  );
}

window.DatasetLanding = DatasetLanding;
