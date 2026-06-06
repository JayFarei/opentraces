// Self-Improving page — connected to a repo; watches for systematic issues
// (intent spikes, SOP violations, alerts) and opens PRs back to the source.

const HEAL_STATUS_COLOR = {
  completed:   "var(--c-git)",
  creating_pr: "var(--c-push)",
  failed:      "var(--c-error, #d24545)",
  skipped:     "var(--fg-mute)",
  running:     "var(--c-user)",
};

function HealStatusBadge({ status }) {
  const labels = {
    completed: "completed",
    creating_pr: "creating PR",
    failed: "failed",
    skipped: "skipped",
    running: "running",
  };
  return (
    <span className={"heal-badge st-" + status}>
      <span className="heal-badge-dot" style={{ background: HEAL_STATUS_COLOR[status] || "var(--fg-mute)" }} />
      <span>{labels[status] || status}</span>
    </span>
  );
}

function SelfImprovingPage({ onSelectRepo }) {
  const [tab, setTab] = React.useState("overview");
  const cfg = HEAL_CONFIG;
  const repo = REPO_DEFS[cfg.repo];
  const okCount   = HEAL_JOBS.filter(j => j.status === "completed").length;
  const failCount = HEAL_JOBS.filter(j => j.status === "failed").length;
  const skipCount = HEAL_JOBS.filter(j => j.status === "skipped").length;
  const inflight  = HEAL_JOBS.filter(j => j.status === "creating_pr" || j.status === "running");

  return (
    <div className="landing landing-page">
      <PageHero
        kicker="Automation"
        title="Self-Improving"
        subtitle="When intent spikes or SOP violations exceed thresholds, the agent drafts a fix, opens a PR, and waits for review. You stay in the loop."
        actions={
          <div className="ph-action-group">
            <ToolBtn icon="git-commit" label="Test heal" />
            <ToolBtn icon="settings" label="Configure" />
          </div>
        }
      />

      <div className="si-tabs">
        {[
          { id: "overview", label: "Overview", icon: "activity" },
          { id: "history",  label: "Job history", icon: "clock" },
          { id: "config",   label: "Configuration", icon: "settings" },
        ].map(t => (
          <button
            key={t.id}
            className="si-tab"
            aria-current={tab === t.id}
            onClick={() => setTab(t.id)}
          >
            <Icon name={t.icon} size={13} />
            <span>{t.label}</span>
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          <div className="si-grid-top">
            <div className="si-conn-card">
              <div className="sic-icon">
                <Icon name="git-branch" size={18} />
              </div>
              <div className="sic-body">
                <div className="sic-repo">
                  <span className="ns">{repo.ns}/</span>
                  <span className="nm">{repo.nm}</span>
                </div>
                <div className="sic-meta mono">
                  <span>{repo.ns}</span>
                  <span className="dot-sep" />
                  <span>{cfg.branch}</span>
                  <span className="dot-sep" />
                  <span>Connected {cfg.connected_at}</span>
                </div>
              </div>
              <div className="sic-status">
                <span className="sic-status-pill active">
                  <span className="sic-dot" />
                  <span>Active</span>
                </span>
                <button className="sic-link" onClick={() => onSelectRepo && onSelectRepo(cfg.repo)}>Open repo →</button>
              </div>
            </div>

            <div className="si-jobs-card">
              <div className="sjc-head">
                <div className="sjc-kicker">Recent jobs</div>
                <div className="sjc-stats">
                  <span className="sjc-stat ok"><span className="mono">{okCount}</span><span>ok</span></span>
                  <span className="sjc-stat fail"><span className="mono">{failCount}</span><span>fail</span></span>
                  <span className="sjc-stat skip"><span className="mono">{skipCount}</span><span>skip</span></span>
                </div>
              </div>
              <div className="sjc-sparkline">
                {HEAL_JOBS.slice().reverse().map((j, i) => (
                  <span
                    key={i}
                    className={"sjc-spark " + j.status}
                    style={{ background: HEAL_STATUS_COLOR[j.status] }}
                    title={`${j.name} · ${j.status}`}
                  />
                ))}
              </div>
            </div>

            <div className="si-config-card">
              <div className="scc-kicker">Configuration</div>
              <div className="scc-model mono">{cfg.model}</div>
              <div className="scc-meta">
                <span className="mono">{cfg.heals_per_day}</span>
                <span>heals/day</span>
                <span className="dot-sep" />
                <span className="mono">{cfg.cooldown_min}min</span>
                <span>cooldown</span>
              </div>
            </div>
          </div>

          {inflight.length > 0 && (
            <div className="si-inflight-card">
              <div className="sif-kicker">In flight</div>
              {inflight.map(j => (
                <div key={j.id} className="sif-row">
                  <HealStatusBadge status={j.status} />
                  <span className="sif-name">{j.name}</span>
                  <span className="sif-when mono">started {j.when}</span>
                  <button className="sif-cancel">Cancel</button>
                </div>
              ))}
            </div>
          )}

          <header className="si-section-head">
            <h3 className="repo-h3">Recent jobs</h3>
            <button className="si-view-all">View all →</button>
          </header>
          <div className="si-job-list">
            {HEAL_JOBS.map(j => (
              <div key={j.id} className="si-job-row">
                <span className="sjr-mark" style={{ background: HEAL_STATUS_COLOR[j.status] }} />
                <div className="sjr-body">
                  <div className="sjr-name">{j.name}</div>
                  <div className="sjr-when mono">{j.when}</div>
                </div>
                <HealStatusBadge status={j.status} />
                <button className="sjr-link" aria-label="Open PR">
                  <Icon name="external" size={12} />
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      {tab === "history" && (
        <div className="si-history">
          <div className="si-job-list full">
            {HEAL_JOBS.concat(HEAL_JOBS).map((j, i) => (
              <div key={i} className="si-job-row">
                <span className="sjr-mark" style={{ background: HEAL_STATUS_COLOR[j.status] }} />
                <div className="sjr-body">
                  <div className="sjr-name">{j.name}</div>
                  <div className="sjr-when mono">{j.when}</div>
                </div>
                <HealStatusBadge status={j.status} />
                <button className="sjr-link" aria-label="Open PR">
                  <Icon name="external" size={12} />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "config" && (
        <div className="si-config-grid">
          <div className="si-cfg-card">
            <div className="rail-h">Model</div>
            <div className="cfg-row">
              <span>Heal model</span>
              <span className="mono">{cfg.model}</span>
            </div>
            <div className="cfg-row">
              <span>Reasoning</span>
              <span className="mono">extended (32k)</span>
            </div>
          </div>
          <div className="si-cfg-card">
            <div className="rail-h">Limits</div>
            <div className="cfg-row">
              <span>Heals per day</span>
              <span className="mono">{cfg.heals_per_day}</span>
            </div>
            <div className="cfg-row">
              <span>Cooldown between heals</span>
              <span className="mono">{cfg.cooldown_min}min</span>
            </div>
            <div className="cfg-row">
              <span>Auto-merge</span>
              <span className="mono">off (PR only)</span>
            </div>
          </div>
          <div className="si-cfg-card">
            <div className="rail-h">Triggers</div>
            <div className="cfg-row"><span>Intent spike</span><span className="mono">+30% over baseline</span></div>
            <div className="cfg-row"><span>SOP violations</span><span className="mono">≥ 50/hr</span></div>
            <div className="cfg-row"><span>Alert fired</span><span className="mono">any with auto-heal flag</span></div>
            <div className="cfg-row"><span>Manual</span><span className="mono">via "Test heal"</span></div>
          </div>
          <div className="si-cfg-card">
            <div className="rail-h">Scope</div>
            <div className="cfg-row"><span>Repo</span><span className="mono">{cfg.repo}</span></div>
            <div className="cfg-row"><span>Branch policy</span><span className="mono">never main</span></div>
            <div className="cfg-row"><span>Reviewers</span><span className="mono">@jayfarei</span></div>
          </div>
        </div>
      )}
    </div>
  );
}

window.SelfImprovingPage = SelfImprovingPage;
