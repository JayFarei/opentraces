// RepoLanding — what's happening in this codebase right now

function WorkingTreeCard({ repo, def, onOpenTrace }) {
  const wt = def.working_tree;
  const total = wt.modified + wt.added + wt.deleted;
  if (total === 0 && !wt.current_trace) {
    return (
      <div className="wt-card empty">
        <div className="wt-empty-ico"><Icon name="git-commit" size={20} /></div>
        <div>
          <div className="wt-empty-h">Working tree clean</div>
          <div className="wt-empty-s">No uncommitted changes. Last push {def.last_push}.</div>
        </div>
      </div>
    );
  }
  return (
    <div className="wt-card">
      <div className="wt-head">
        <span className="wt-pulse" />
        <span className="wt-h">Working tree</span>
        <span className="wt-branch mono">on {def.default_branch}</span>
      </div>
      <div className="wt-body">
        <div className="wt-counts">
          {wt.modified > 0 && <div className="wt-c modified"><span className="mono">{wt.modified}</span><span className="lbl">modified</span></div>}
          {wt.added > 0 && <div className="wt-c added"><span className="mono">{wt.added}</span><span className="lbl">added</span></div>}
          {wt.deleted > 0 && <div className="wt-c deleted"><span className="mono">{wt.deleted}</span><span className="lbl">deleted</span></div>}
        </div>
        {wt.current_trace && (
          <button className="wt-trace" onClick={() => onOpenTrace(wt.current_trace.id)}>
            <span className="wt-tlbl">In flight</span>
            <span className="wt-ttitle">{wt.current_trace.title}</span>
            <span className="mono wt-tid">{wt.current_trace.id}</span>
          </button>
        )}
      </div>
    </div>
  );
}

function RepoLanding({ repoId, onSelectTrace }) {
  const def = REPO_DEFS[repoId];
  if (!def) return null;
  // pick traces for this repo from RECENT_TRACES; fall back to a few synthetic
  const repoTraces = React.useMemo(() => RECENT_TRACES.filter(t => t.repo === repoId).slice(0, 8), [repoId]);
  const fallback = repoTraces.length === 0 ? RECENT_TRACES.slice(0, 4).map(t => ({ ...t, repo: repoId })) : repoTraces;

  return (
    <div className="landing landing-repo">
      <header className="repo-hero">
        <div className="rh-icon"><Icon name="git-branch" size={18} /></div>
        <div className="rh-title">
          <h1 className="rh-name"><span className="ns">{def.ns}/</span><span className="nm">{def.nm}</span></h1>
          <p className="rh-desc">{def.description}</p>
        </div>
        <div className="rh-meta">
          <div className="rh-stat"><div className="k">Branch</div><div className="v mono">{def.default_branch}</div></div>
          <div className="rh-stat"><div className="k">Open traces</div><div className="v mono">{def.open_traces}</div></div>
          <div className="rh-stat"><div className="k">Total</div><div className="v mono">{def.total_traces}</div></div>
          <div className="rh-stat"><div className="k">Last push</div><div className="v">{def.last_push}</div></div>
        </div>
      </header>

      <WorkingTreeCard repo={repoId} def={def} onOpenTrace={onSelectTrace} />

      <div className="repo-grid">
        <section className="repo-col">
          <h3 className="repo-h3">Recent traces</h3>
          <TracesTable traces={fallback} groupBy="day" onSelect={onSelectTrace} hideRepo />
        </section>
        <aside className="repo-side">
          <div className="rail-card">
            <div className="rail-h">Datasets</div>
            {(!def.datasets || def.datasets.length === 0) && <div className="rail-empty">No datasets yet</div>}
            {def.datasets && def.datasets.map(ds => (
              <div key={ds} className="rail-row">
                <Icon name="datasets" size={12} />
                <span>{DATASET_DEFS[ds]?.name || ds}</span>
                <span className={"remote-tag remote-" + (DATASET_DEFS[ds]?.remote || "local")}>{DATASET_DEFS[ds]?.remote}</span>
              </div>
            ))}
          </div>
          <div className="rail-card">
            <div className="rail-h">Stats</div>
            <div className="rail-row"><span>Contributors</span><span className="mono rail-meta">{def.contributors}</span></div>
            <div className="rail-row"><span>Open</span><span className="mono rail-meta">{def.open_traces}</span></div>
            <div className="rail-row"><span>Total</span><span className="mono rail-meta">{def.total_traces}</span></div>
          </div>
        </aside>
      </div>
    </div>
  );
}

window.RepoLanding = RepoLanding;
