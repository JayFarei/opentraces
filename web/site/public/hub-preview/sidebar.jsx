// Sidebar — workspace nav. Top items: Traces (landing), Datasets, Repositories.
// Search is a sidebar item (global, jumps to results).

const REPOS = [
  { id: "jayfarei/opentraces", ns: "JayFarei", nm: "opentraces", active: true },
  { id: "jayfarei/datafetch", ns: "JayFarei", nm: "datafetch" },
  { id: "jayfarei/lazymem", ns: "JayFarei", nm: "lazymem" },
  { id: "jayfarei/clarify", ns: "JayFarei", nm: "clarify" },
  { id: "jayfarei/open-data", ns: "JayFarei", nm: "open-data" },
  { id: "jayfarei/koreader-kiosk", ns: "JayFarei", nm: "koreader-kiosk" },
];

const REPO_CHILDREN = [
  { id: "overview", label: "Overview" },
  { id: "traces", label: "Traces" },
  { id: "pulls", label: "Pull requests" },
  { id: "trails", label: "Trails" },
  { id: "settings", label: "Settings" },
];

// Datasets each carry their workflow definition inline (workflows are no longer top-level).
// Inbox: produced items not yet reviewed. Auto-policy datasets always have inbox=0.
const DATASETS = [
  { id: "ds-claude-eval-v3",   name: "claude-eval-v3",   rows: "12.4k", remote: "synced", inbox: 0 },
  { id: "ds-keystrokes-2026",  name: "keystrokes-2026",  rows: "8.1k",  remote: "synced", inbox: 0 },
  { id: "ds-edge-traces",      name: "edge-traces",      rows: "2.3k",  remote: "ahead",  inbox: 12 },
  { id: "ds-rag-fixtures",     name: "rag-fixtures",     rows: "947",   remote: "behind", inbox: 4 },
  { id: "ds-shortcuts-bench",  name: "shortcuts-bench",  rows: "312",   remote: "local",  inbox: 27 },
];

const DATASET_CHILDREN = [
  { id: "overview", label: "Overview" },
  { id: "inbox",    label: "Inbox" },
  { id: "workflow", label: "Workflow" },
];

const STATUS_COLOR = {
  committed: "var(--c-git)",
  working:   "var(--c-user)",
  pushed:    "var(--c-push)",
  failed:    "var(--c-fail, #d24545)",
  ok:        "var(--c-git)",
  warn:      "var(--c-warn, #d9a441)",
  synced:    "var(--c-git)",
  ahead:     "var(--c-push)",
  behind:    "var(--c-warn, #d9a441)",
  local:     "var(--fg-mute)",
};

const REMOTE_LABEL = {
  synced: "synced",
  ahead:  "ahead",
  behind: "behind",
  local:  "local only",
};

function SectionHeader({ id, label, icon, count, expanded, onToggle, onAdd, addTitle }) {
  return (
    <div className="sb-sec-head">
      <button
        className="sb-sec-toggle"
        data-expanded={expanded}
        aria-expanded={expanded}
        onClick={() => onToggle(id)}
      >
        <Icon name="chevron-down" size={11} className="chev" />
        <Icon name={icon} size={14} className="sicon" />
        <span className="lbl">{label}</span>
        {typeof count === "number" && <span className="count">{count}</span>}
      </button>
      {onAdd && (
        <button className="add" aria-label={addTitle} title={addTitle} onClick={onAdd}>
          <Icon name="plus" size={12} />
        </button>
      )}
    </div>
  );
}

function FlatGroupLabel({ label, count, onAdd, addTitle }) {
  return (
    <div className="sb-flat-group-head">
      <span className="sb-flat-group-label">{label}</span>
      {typeof count === "number" && <span className="sb-flat-group-count mono">{count}</span>}
      {onAdd && (
        <button className="sb-flat-group-add" aria-label={addTitle} title={addTitle} onClick={onAdd}>
          <Icon name="plus" size={12} />
        </button>
      )}
    </div>
  );
}

function StatusDot({ status }) {
  return <span className="sb-status-dot" style={{ background: STATUS_COLOR[status] || "var(--fg-mute)" }} />;
}

function DatasetsSection({ expandedDataset, onExpandDataset, activeId, activeChild, onSelectDataset, onSelectChild, collapsed }) {
  return (
    <div className="sb-section flat">
      {!collapsed && (
        <FlatGroupLabel
          label="Datasets"
          count={DATASETS.length}
          onAdd={() => {}}
          addTitle="Add dataset"
        />
      )}
      <div className="sb-section-body flat">
        <div className="sb-list repos">
          {DATASETS.map(d => {
            const isExpanded = expandedDataset === d.id;
            return (
              <div key={d.id} className="sb-repo">
                <button
                  className="sb-repo-head ds-head"
                  data-expanded={isExpanded}
                  onClick={() => onExpandDataset(d.id)}
                >
                  <Icon name="chevron-down" size={11} className="chev" />
                  <StatusDot status={d.remote} />
                  <span className="rname">
                    <span className="nm">{d.name}</span>
                  </span>
                  <span className="ds-sub">
                    <span className="mono">{d.rows}</span>
                    {d.inbox > 0 && <span className="inbox-pill" title={`${d.inbox} in inbox`}>{d.inbox}</span>}
                  </span>
                </button>
                {isExpanded && (
                  <div className="sb-repo-children">
                    {DATASET_CHILDREN.map(child => (
                      <button
                        key={child.id}
                        className="sb-repo-child"
                        aria-current={activeId === d.id && activeChild === child.id}
                        onClick={() => onSelectChild(d.id, child.id)}
                      >
                        {child.label}
                        {child.id === "inbox" && d.inbox > 0 && (
                          <span className="inbox-pill child">{d.inbox}</span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function RepositoriesSection({ expandedRepo, onExpandRepo, activeRepo, activeRepoChild, onSelectRepoChild, collapsed }) {
  return (
    <div className="sb-section flat">
      {!collapsed && (
        <FlatGroupLabel
          label="Projects"
          count={REPOS.length}
          onAdd={() => {}}
          addTitle="Add a project"
        />
      )}
      <div className="sb-section-body flat">
        <div className="sb-list repos">
          {REPOS.map(repo => {
            const isExpanded = expandedRepo === repo.id;
            return (
              <div key={repo.id} className="sb-repo">
                <button
                  className="sb-repo-head"
                  data-expanded={isExpanded}
                  onClick={() => onExpandRepo(repo.id)}
                >
                  <Icon name="chevron-down" size={11} className="chev" />
                  <Icon name="git-branch" size={14} className="ricon" />
                  <span className="rname">
                    <span className="ns">{repo.ns}/</span>
                    <span className="nm">{repo.nm}</span>
                  </span>
                </button>
                {isExpanded && (
                  <div className="sb-repo-children">
                    {REPO_CHILDREN.map(child => (
                      <button
                        key={child.id}
                        className="sb-repo-child"
                        aria-current={activeRepo === repo.id && activeRepoChild === child.id}
                        onClick={() => onSelectRepoChild(repo.id, child.id)}
                      >
                        {child.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Sidebar({
  activeNav, onNav,
  expandedSections, onToggleSection,
  searchQuery, onSearchQuery, onActivateSearch,
  expandedDataset, onExpandDataset, activeDatasetId, activeDatasetChild, onSelectDatasetChild,
  expandedRepo, onExpandRepo, activeRepo, activeRepoChild, onSelectRepoChild,
  collapsed, onToggleCollapsed,
}) {
  const isExp = (id) => expandedSections.includes(id);

  return (
    <aside className="sidebar" data-testid="sidebar" data-collapsed={collapsed ? "true" : "false"}>
      <div className="sb-brand">
        {!collapsed && (
          <span className="wm">
            <span className="open">open</span>
            <span className="traces">traces</span>
            <span className="hub-tag">Hub</span>
          </span>
        )}
        <button
          className="sb-collapse-btn"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={onToggleCollapsed}
        >
          <Icon name={collapsed ? "chevron-right" : "chevron-left"} size={14} />
        </button>
      </div>

      {/* Search — opens the global command palette (⌘K) */}
      <div className="sb-global-search">
        <button
          type="button"
          className="sb-search-input"
          onClick={onActivateSearch}
          title="Search everything (⌘K)"
        >
          <Icon name="search" size={13} className="sicon" />
          <span className="sb-search-ph">Search traces, datasets…</span>
          <kbd className="sb-kbd">⌘K</kbd>
        </button>
      </div>

      {/* Top-level: Overview (ledger of traces, ties to projects + datasets) */}
      <nav className="sb-nav" aria-label="Workspace">
        <button
          className="sb-nav-item"
          aria-current={activeNav === "traces"}
          onClick={() => onNav("traces")}
        >
          <Icon name="grid" size={16} className="icon" />
          <span>Overview</span>
        </button>
      </nav>

      {!collapsed && <div className="sb-group-label">Trace intelligence <span className="pro-badge">PRO</span></div>}
      <nav className="sb-nav-sub" aria-label="Intelligence">
        <button className="sb-nav-item" aria-current={activeNav === "spotlight"} onClick={() => onNav("spotlight")} title="Spotlight">
          <Icon name="sparkles" size={15} className="icon" />
          <span>Spotlight</span>
        </button>
        <button className="sb-nav-item" aria-current={activeNav === "intents"} onClick={() => onNav("intents")} title="Intents">
          <Icon name="tag" size={15} className="icon" />
          <span>Intents</span>
        </button>
        <button className="sb-nav-item" aria-current={activeNav === "evals"} onClick={() => onNav("evals")} title="Evals">
          <Icon name="shield" size={15} className="icon" />
          <span>Evals</span>
        </button>
        <button className="sb-nav-item" aria-current={activeNav === "capsules"} onClick={() => onNav("capsules")} title="Capsules">
          <Icon name="capsule" size={15} className="icon" />
          <span>Capsules</span>
        </button>
        <button className="sb-nav-item" aria-current={activeNav === "alerts"} onClick={() => onNav("alerts")} title="Alerts">
          <Icon name="bell" size={15} className="icon" />
          <span>Alerts</span>
        </button>
        <button className="sb-nav-item" aria-current={activeNav === "improving"} onClick={() => onNav("improving")} title="Self-Improving">
          <Icon name="heart-pulse" size={15} className="icon" />
          <span>Self-Improving</span>
        </button>
      </nav>

      <div className="sb-tree">
        <DatasetsSection
          collapsed={collapsed}
          expandedDataset={expandedDataset}
          onExpandDataset={onExpandDataset}
          activeId={activeDatasetId}
          activeChild={activeDatasetChild}
          onSelectChild={onSelectDatasetChild}
        />
        <RepositoriesSection
          collapsed={collapsed}
          expandedRepo={expandedRepo}
          onExpandRepo={onExpandRepo}
          activeRepo={activeRepo}
          activeRepoChild={activeRepoChild}
          onSelectRepoChild={onSelectRepoChild}
        />
      </div>

      <div className="sb-foot">
        <button className="sb-nav-item">
          <Icon name="settings" size={15} className="icon" />
          <span>Settings</span>
        </button>
      </div>
    </aside>
  );
}

window.Sidebar = Sidebar;
