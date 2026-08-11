// Entity indexes — canonical full lists at Jayfarei / Datasets and
// Jayfarei / Projects. The sidebar shows only the most recent few entities;
// these pages are built to keep working at 50+ (filter + full metadata).

function EntityIndexToolbar({ q, onQ, placeholder, shown, total, noun }) {
  return (
    <div className="eix-toolbar">
      <label className="eix-search">
        <Icon name="search" size={13} className="sicon" />
        <input
          type="text"
          placeholder={placeholder}
          value={q}
          onChange={(e) => onQ(e.target.value)}
        />
      </label>
      <span className="eix-count mono">
        {q ? `${shown} of ${total}` : total} {noun}
      </span>
    </div>
  );
}

function DatasetsIndexPage({ onSelectDataset }) {
  const [q, setQ] = React.useState("");
  const all = React.useMemo(
    () => Object.entries(window.DATASET_DEFS || {}).map(([id, d]) => ({ id, ...d })), []);
  const list = all.filter((d) =>
    !q || (d.name + " " + (d.description || "")).toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="landing landing-entity-index">
      <PageHero
        kicker="Workspace"
        title="Datasets"
        subtitle="Every dataset in this workspace. The sidebar shows only the most recently active ones."
        actions={<ToolBtn icon="plus" label="New dataset" primary data-agent-action="new-dataset" data-agent-desc="Create a new dataset" data-agent-mutates="true" onClick={() => {}} />}
      />
      <div className="ov-dense">
        <EntityIndexToolbar q={q} onQ={setQ} placeholder="Filter datasets…" shown={list.length} total={all.length} noun="datasets" />
        <div className="eix-list">
          {list.map((d) => (
            <button key={d.id} className="eix-row" onClick={() => onSelectDataset(d.id)}>
              <Icon name="datasets" size={15} className="eix-icon" />
              <span className="eix-main">
                <span className="eix-name mono">{d.name}</span>
                <span className="eix-desc">{d.description}</span>
              </span>
              <span className="eix-meta">
                <span className="eix-m">
                  <span className="v">{(d.rows || 0).toLocaleString()}</span>
                  <span className="k">rows</span>
                </span>
                <span className={"eix-m" + (d.inbox_count ? " hot" : " zero")}>
                  <span className="v">{d.inbox_count}</span>
                  <span className="k">inbox</span>
                </span>
                <span className="eix-m wide">
                  <span className="v">{d.last_update}</span>
                  <span className="k">updated</span>
                </span>
                <span className={"remote-tag remote-" + (d.remote || "local")}>
                  {d.remote === "local" ? "local only" : d.remote}
                </span>
              </span>
            </button>
          ))}
          {list.length === 0 && (
            <div className="eix-empty">No datasets match “{q}”.</div>
          )}
        </div>
      </div>
    </div>
  );
}

function ProjectsIndexPage({ onSelectRepo }) {
  const [q, setQ] = React.useState("");
  const all = React.useMemo(
    () => Object.entries(window.REPO_DEFS || {}).map(([id, d]) => ({ id, ...d })), []);
  const list = all.filter((r) =>
    !q || (r.id + " " + (r.description || "")).toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="landing landing-entity-index">
      <PageHero
        kicker="Workspace"
        title="Projects"
        subtitle="Every connected repository. The sidebar shows only the most recently active ones."
        actions={<ToolBtn icon="plus" label="Add a project" primary onClick={() => {}} />}
      />
      <div className="ov-dense">
        <EntityIndexToolbar q={q} onQ={setQ} placeholder="Filter projects…" shown={list.length} total={all.length} noun="projects" />
        <div className="eix-list">
          {list.map((r) => (
            <button key={r.id} className="eix-row" onClick={() => onSelectRepo(r.id)}>
              <Icon name="git-branch" size={15} className="eix-icon" />
              <span className="eix-main">
                <span className="eix-name">
                  <span className="ns">{r.ns}/</span>
                  <span className="nm">{r.nm}</span>
                </span>
                <span className="eix-desc">{r.description}</span>
              </span>
              <span className="eix-meta">
                <span className="eix-m">
                  <span className="v">{r.total_traces}</span>
                  <span className="k">traces</span>
                </span>
                <span className={"eix-m" + (r.open_traces ? " hot" : " zero")}>
                  <span className="v">{r.open_traces}</span>
                  <span className="k">open</span>
                </span>
                <span className="eix-m">
                  <span className="v">{r.contributors}</span>
                  <span className="k">people</span>
                </span>
                <span className="eix-m wide">
                  <span className="v">{r.last_push}</span>
                  <span className="k">pushed</span>
                </span>
              </span>
            </button>
          ))}
          {list.length === 0 && (
            <div className="eix-empty">No projects match “{q}”.</div>
          )}
        </div>
      </div>
    </div>
  );
}

window.DatasetsIndexPage = DatasetsIndexPage;
window.ProjectsIndexPage = ProjectsIndexPage;
