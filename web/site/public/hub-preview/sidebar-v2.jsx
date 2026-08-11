// sidebar-v2.jsx — v40-aligned workspace nav, project-domain edition.
//   · Atlas, Bench, Evidence live under each PROJECT (one bench per project).
//   · PROJECTIONS is the umbrella for everything derived from the app and
//     its agentic work: Datasets (static — materialized rows) and Arenas
//     (stateful — a bench plays a scenario, a capsule plays a trace, a gym
//     plays a task family). Canonical home vs index: a bench LIVES in its
//     project; it merely SURFACES in the Arenas group/index with a link back.
//   · Boxes is workspace-level (top nav) — it's where box credits live.

const SBV2_REPOS = [
  { id: "jayfarei/opentraces", ns: "JayFarei", nm: "opentraces", active: true },
  { id: "jayfarei/datafetch", ns: "JayFarei", nm: "datafetch" },
  { id: "jayfarei/lazymem", ns: "JayFarei", nm: "lazymem" },
  { id: "jayfarei/clarify", ns: "JayFarei", nm: "clarify" },
  { id: "jayfarei/open-data", ns: "JayFarei", nm: "open-data" },
  { id: "jayfarei/koreader-kiosk", ns: "JayFarei", nm: "koreader-kiosk" },
];

const SBV2_REPO_CHILDREN = [
  { id: "overview", label: "Overview" },
  { id: "traces", label: "Traces" },
  { id: "pulls", label: "Pull requests" },
  { id: "bench", label: "Bench", badge: (repoId) => repoId === (window.ACCOUNT_HOME_REPO || "jayfarei/opentraces") ? (window.V2_EVIDENCE || []).filter(e => e.unread).length : 0 },
  { id: "settings", label: "Settings" },
];

const SBV2_DATASETS = [
  { id: "ds-claude-eval-v3",   name: "claude-eval-v3",   rows: "12.4k", remote: "synced", inbox: 0 },
  { id: "ds-keystrokes-2026",  name: "keystrokes-2026",  rows: "8.1k",  remote: "synced", inbox: 0 },
  { id: "ds-edge-traces",      name: "edge-traces",      rows: "2.3k",  remote: "ahead",  inbox: 12 },
  { id: "ds-rag-fixtures",     name: "rag-fixtures",     rows: "947",   remote: "behind", inbox: 4 },
  { id: "ds-shortcuts-bench",  name: "shortcuts-bench",  rows: "312",   remote: "local",  inbox: 27 },
];

const SBV2_DATASET_CHILDREN = [
  { id: "overview", label: "Overview" },
  { id: "inbox",    label: "Inbox" },
  { id: "workflow", label: "Workflow" },
];

const SBV2_STATUS_COLOR = {
  synced: "var(--c-git)", ahead: "var(--c-push)", behind: "var(--c-warn, #d9a441)", local: "var(--fg-mute)",
};

const SBV2_RECENT_N = 4;

// Account-aware lists — applyAccountData() swaps window.REPO_DEFS /
// window.DATASET_DEFS when switching personal ⇄ org, so derive from
// those at render time instead of the hardcoded personal seeds.
function sbv2FmtRows(n) {
  return n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k" : String(n);
}
function sbv2Repos() {
  const defs = window.REPO_DEFS;
  if (!defs) return SBV2_REPOS;
  const home = window.ACCOUNT_HOME_REPO;
  return Object.keys(defs).map((id) => ({ id, ns: defs[id].ns, nm: defs[id].nm, active: id === home }));
}
function sbv2Datasets() {
  const defs = window.DATASET_DEFS;
  if (!defs) return SBV2_DATASETS;
  return Object.keys(defs).map((id) => ({
    id,
    name: defs[id].name,
    rows: sbv2FmtRows(defs[id].rows || 0),
    remote: defs[id].remote,
    inbox: defs[id].inbox_count || 0,
  }));
}

function Sbv2StatusDot({ status }) {
  return <span className="sb-status-dot" style={{ background: SBV2_STATUS_COLOR[status] || "var(--fg-mute)" }} />;
}

// PROJECTIONS — one menu for everything derived from the app and its work.
// Four kind rows expand in place (accordion), reusing the project-row
// vocabulary: chevron head, indented children. Canonical homes are
// respected — bench children open their PROJECT's bench tab.
function Sbv2Projections({ collapsed, onNav, onOpenDataset, activeDatasetId, onOpenBench, activeRepo, activeRepoChild, onOpenCapsule, activeCapsuleId }) {
  const [groupClosed, onToggleGroup] = useGroupCollapsed("projections");
  const [openKind, setOpenKind] = React.useState("datasets");
  const caps = window.CAPSULES || [];
  const gyms = window.V2_GYMS || [];
  const home = window.ACCOUNT_HOME_REPO || "jayfarei/opentraces";
  const unread = (window.V2_EVIDENCE || []).filter(e => e.unread).length;
  const dsList = sbv2Datasets();
  const repoList = sbv2Repos();
  const dsInbox = dsList.reduce((n, d) => n + d.inbox, 0);
  const kinds = [
    { id: "datasets", icon: "datasets", label: "Datasets", count: dsList.length,  badge: dsInbox, hint: "Static projections — rows materialized by a workflow over traces + labels" },
    { id: "benches",  icon: "play",     label: "Benches",  count: repoList.length, badge: unread,  hint: "Stateful — each plays a scenario; one per project, lives in the project" },
    { id: "capsules", icon: "capsule",  label: "Capsules", count: caps.length,          badge: 0,       hint: "Stateful — each plays a trace; sealed runs that travel" },
    { id: "gyms",     icon: "arena",    label: "Gyms",     count: gyms.length,          badge: 0,       hint: "Stateful — each plays a task family harvested from real slices" },
  ];
  const total = kinds.reduce((n, k) => n + k.count, 0);
  if (collapsed) return <RailGroupIcon icon="datasets" label="Projections" onClick={() => onNav("projections-index")} />;
  return (
    <div className="sb-section flat">
      <FlatGroupLabel label="Projections" count={total} onAdd={() => {}} addTitle="New projection" open={!groupClosed} onToggle={onToggleGroup} />
      {!groupClosed && (
        <div className="sb-section-body flat">
          <div className="sb-list repos">
            {kinds.map(k => {
              const isOpen = openKind === k.id;
              return (
                <div key={k.id} className="sb-repo">
                  <button className="sb-repo-head" data-expanded={isOpen} title={k.hint} onClick={() => setOpenKind(isOpen ? null : k.id)}>
                    <Icon name="chevron-down" size={11} className="chev" />
                    <Icon name={k.icon} size={13} className="ricon" />
                    <span className="rname"><span className="nm">{k.label}</span></span>
                    <span className="ds-sub">
                      <span className="mono">{k.count}</span>
                      {k.badge > 0 && <span className="inbox-pill">{k.badge}</span>}
                    </span>
                  </button>
                  {isOpen && (
                    <div className="sb-repo-children">
                      {k.id === "datasets" && dsList.slice(0, SBV2_RECENT_N).map(d => (
                        <button key={d.id} className="sb-repo-child sb-child-clip" aria-current={activeDatasetId === d.id} onClick={() => onOpenDataset(d.id)}>
                          {d.name}
                          {d.inbox > 0 && <span className="inbox-pill child">{d.inbox}</span>}
                        </button>
                      ))}
                      {k.id === "datasets" && (
                        <button className="sb-repo-child sb-child-all" onClick={() => onNav("datasets-index")}>All datasets →</button>
                      )}
                      {k.id === "benches" && repoList.map(r => (
                        <button key={r.id} className="sb-repo-child sb-child-clip" aria-current={activeRepo === r.id && activeRepoChild === "bench"} title={"Lives in " + r.id + " — opens the project bench"} onClick={() => onOpenBench(r.id)}>
                          {r.nm}
                          {r.id === home && unread > 0 && <span className="inbox-pill child" title={unread + " unread evidence records"}>{unread}</span>}
                        </button>
                      ))}
                      {k.id === "capsules" && caps.slice(0, SBV2_RECENT_N).map(c => (
                        <button key={c.id} className="sb-repo-child sb-child-clip" aria-current={activeCapsuleId === c.id} title={c.title + " · plays a trace · sealed " + c.publishedAt} onClick={() => onOpenCapsule(c.id)}>{c.title}</button>
                      ))}
                      {k.id === "capsules" && caps.length > SBV2_RECENT_N && (
                        <button className="sb-repo-child sb-child-all" onClick={() => onNav("capsules")}>All capsules →</button>
                      )}
                      {k.id === "gyms" && gyms.map(g => (
                        <button key={g.id} className="sb-repo-child sb-child-clip" title={g.job + " · plays a task family · " + g.tasks + " tasks · " + Math.round(g.passRate * 100) + "% pass"}>{g.name}</button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            <button className="sb-view-all" onClick={() => onNav("projections-index")} title="One table of every projection — filter by kind">
              <span>Browse all projections</span>
              <span className="count mono">{total}</span>
              <Icon name="chevron-right" size={11} className="go" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Sbv2Repos({ expandedRepo, onExpandRepo, activeRepo, activeRepoChild, onSelectRepoChild, collapsed, onViewAll }) {
  const [groupClosed, onToggleGroup] = useGroupCollapsed("projects");
  const repoList = sbv2Repos();
  const recent = repoList.slice(0, SBV2_RECENT_N);
  if (collapsed) return <RailGroupIcon icon="git-branch" label="Projects" onClick={onViewAll} />;
  return (
    <div className="sb-section flat">
      <FlatGroupLabel label="Projects" count={repoList.length} onAdd={() => {}} addTitle="Add a project" open={!groupClosed} onToggle={onToggleGroup} />
      {!groupClosed && (
        <div className="sb-section-body flat">
          <div className="sb-list repos">
            {recent.map(repo => {
              const isExpanded = expandedRepo === repo.id;
              return (
                <div key={repo.id} className="sb-repo">
                  <button className="sb-repo-head" data-expanded={isExpanded} onClick={() => onExpandRepo(repo.id)}>
                    <Icon name="chevron-down" size={11} className="chev" />
                    <Icon name="git-branch" size={14} className="ricon" />
                    <span className="rname"><span className="ns">{repo.ns}/</span><span className="nm">{repo.nm}</span></span>
                  </button>
                  {isExpanded && (
                    <div className="sb-repo-children">
                      {SBV2_REPO_CHILDREN.map(child => {
                        const n = child.badge ? child.badge(repo.id) : 0;
                        return (
                          <button key={child.id} className="sb-repo-child" aria-current={activeRepo === repo.id && activeRepoChild === child.id} onClick={() => onSelectRepoChild(repo.id, child.id)}>
                            {child.label}
                            {n > 0 && <span className="inbox-pill child" title={n + " unread evidence records"}>{n}</span>}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
            {repoList.length > recent.length && (
              <button className="sb-view-all" onClick={onViewAll} title={`Showing ${recent.length} recent · ${repoList.length} total`}>
                <span>View all projects</span>
                <span className="count mono">{repoList.length}</span>
                <Icon name="chevron-right" size={11} className="go" />
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const SBP_MEMBERS = [
  { name: "Gabriele Farei", role: "Admin",  color: "var(--c-write)", traces: 212, capsules: 3, datasets: 2 },
  { name: "Mara Voss",      role: "Admin",  color: "var(--c-read)",  traces: 147, capsules: 2, datasets: 3 },
  { name: "Otto",           role: "Agent",  color: "var(--c-exec)",  traces: 486, capsules: 5, datasets: 1 },
  { name: "Wei Zhang",      role: "Member", color: "var(--c-git)",   traces: 88,  capsules: 1, datasets: 1 },
  { name: "Nadia Flores",   role: "Member", color: "var(--c-plan)",  traces: 64,  capsules: 2, datasets: 0 },
];

// Members panel — replaces the nav content below the card (down to the
// menu/Claude switch) so the roster feels part of the sidebar, not a popup.
function SbpMembersPanel({ onClose, onNav }) {
  React.useEffect(() => {
    const onEsc = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onEsc);
    return () => document.removeEventListener("keydown", onEsc);
  }, []);
  const go = (page) => { onClose(); onNav(page); };
  return (
    <div className="sbp-panel">
      <div className="sbp-panel-h">
        <button className="v2-back" onClick={onClose}><Icon name="back" size={13} /> Members <span className="mono sbp-panel-n">{SBP_MEMBERS.length}</span></button>
      </div>
      <div className="sbp-panel-list">
        {SBP_MEMBERS.map(m => (
          <div key={m.name} className="sbp-pop-row">
            <span className="prof-who-dot" style={{ background: m.color }} />
            <span className="sbp-pop-name">
              {m.name}
              {m.role === "Agent" && <Icon name="bot" size={11} className="prof-who-bot" />}
              <span className="sbp-pop-role">{m.role}</span>
            </span>
            <span className="sbp-pop-links">
              <button className="sbp-pop-chip" title={m.name + " — their filtered view of the trace ledger"} onClick={() => go("traces-index")}>
                {m.traces} traces
              </button>
              {m.capsules > 0 && (
                <button className="sbp-pop-chip" title={m.name + " — capsules they sealed"} onClick={() => go("capsules")}>
                  {m.capsules} caps
                </button>
              )}
              {m.datasets > 0 && (
                <button className="sbp-pop-chip" title={m.name + " — datasets they curate"} onClick={() => go("datasets-index")}>
                  {m.datasets} ds
                </button>
              )}
            </span>
          </div>
        ))}
      </div>
      <div className="sbp-pop-foot">Each entry opens that member's personal filtered view — only what they created.</div>
    </div>
  );
}

// The avatar deck — ONE fixed stack shared by both panes, so the pane
// slide never moves the icons. The clicked card physically moves on top;
// the illusion of stacked cards never breaks.
// NB: DeckCard lives at module scope — defining it inside AccountDeck would
// give React a new component type each render, remounting the DOM nodes and
// making the logos TELEPORT instead of travelling to their target slot.
function DeckCard({ id, front, onSwitch, onOpenSettings, children }) {
  return (
    <button
      className={"deck-card deck-" + id + " " + (front ? "pos-front" : "pos-back")}
      title={front
        ? (id === "personal" ? "Profile settings" : "Organisation settings")
        : (id === "personal" ? "Switch to Jay Farei (personal)" : "Switch to OpenMake (organisation)")}
      aria-label={id === "personal" ? "Personal account" : "OpenMake organisation"}
      onClick={() => front ? (onOpenSettings && onOpenSettings(id)) : onSwitch(id)}
    >
      {children}
      {front && <span className="deck-gear" aria-hidden="true"><Icon name="settings" size={20} /></span>}
    </button>
  );
}

function AccountDeck({ account, onSwitch, onOpenSettings }) {
  const isOrg = account === "org";
  // After a switch the pointer lands on the (new) front card without the
  // user actually aiming at it — mute the settings hover until the mouse
  // leaves the deck once.
  const [hoverMuted, setHoverMuted] = React.useState(false);
  const handleSwitch = (id) => { setHoverMuted(true); onSwitch(id); };
  return (
    <div className={"acct-deck" + (hoverMuted ? " hover-muted" : "")} onMouseLeave={() => setHoverMuted(false)}>
      <DeckCard id="personal" front={!isOrg} onSwitch={handleSwitch} onOpenSettings={onOpenSettings}>
        <img className="sbp-face p" style={{ opacity: 1 }} src="assets/avatar-jf.png" alt="" />
      </DeckCard>
      <DeckCard id="org" front={isOrg} onSwitch={handleSwitch} onOpenSettings={onOpenSettings}>
        <span className="sbp-face o deck-o" style={{ opacity: 1 }}><span className="om-sq" /></span>
      </DeckCard>
    </div>
  );
}

// Sidebar profile card — the account lives here, simply. Flipping to the
// organisation shifts the ambient and morphs the avatar circle -> rounded
// square carrying the OpenMake mark.
function SbProfileCard({ account, onSwitch, onNav, membersOpen, onToggleMembers }) {
  const isOrg = account === "org";
  return (
    <div className={"sbp" + (isOrg ? " org" : "")}>
      <div className="sbp-id deck-space">
        <span className="sbp-name">{isOrg ? "OpenMake" : "Jay Farei"}<span className="gs-acct-type sbp-acct"><Icon name={isOrg ? "users" : "user"} size={10} /><span>{isOrg ? "organisation" : "personal"}</span></span></span>
        <span className="sbp-handle mono">
          {isOrg ? "@openmake" : "@jayfarei"}
          <span className="dot-sep" />
          <Icon name={isOrg ? "globe" : "git"} size={11} /> {isOrg ? "openmake.ai" : "GitHub"}
          {!isOrg && (
            <>
              <span className="dot-sep" />
              <img className="sbp-hf" src="assets/hf-logo.svg" alt="" width="12" height="12" draggable="false" /> jayfarei
            </>
          )}
          {isOrg && (
            <>
              <span className="dot-sep" />
              <button className="sbp-members-btn" aria-expanded={membersOpen} onClick={(e) => { e.stopPropagation(); onToggleMembers(); }} title="Show members">
                <Icon name="user" size={11} /> {SBP_MEMBERS.length}
              </button>
            </>
          )}
        </span>
        <span className="sbp-bio">
          {isOrg
            ? "Building OpenTraces. Agent evidence, custody-first — traces are the new source files."
            : "Release prep and incident follow-up — most of my traces are PR evidence and bench runs on opentraces."}
        </span>
      </div>
    </div>
  );
}

function Sbv2Item({ id, icon, label, tech, activeNav, onNav }) {
  return (
    <button
      className="sb-nav-item"
      aria-current={activeNav === id}
      onClick={() => onNav(id)}
      title={tech ? label + " · " + tech : label}
    >
      <Icon name={icon} size={15} className="icon" />
      <span>{label}</span>
      {tech && <span className="sb-tech mono">{tech}</span>}
    </button>
  );
}

function SidebarV2({
  activeNav, onNav,
  onActivateSearch,
  account, onSwitchAccount,
  expandedDataset, onExpandDataset, activeDatasetId, activeDatasetChild, onSelectDatasetChild,
  expandedRepo, onExpandRepo, activeRepo, activeRepoChild, onSelectRepoChild,
  onSelectArtifact, activeArtifactId,
  onOpenCapsule, activeCapsuleId,
  collapsed, onToggleCollapsed,
}) {
  const [membersOpen, setMembersOpen] = React.useState(false);
  return (
    <aside className="sidebar sidebar-v2" data-testid="sidebar" data-collapsed={collapsed ? "true" : "false"}>
      {!collapsed && (
        <div className="sbp-wrap">
          <SbProfileCard account={account} onSwitch={onSwitchAccount} onNav={onNav} membersOpen={membersOpen} onToggleMembers={() => setMembersOpen(o => !o)} />
          {typeof onToggleCollapsed === "function" && (
            <button className="sb-collapse-btn sbp-collapse" aria-label="Collapse sidebar" title="Collapse sidebar" onClick={onToggleCollapsed}>
              <Icon name="chevron-left" size={14} />
            </button>
          )}
        </div>
      )}
      {collapsed && (
        <span className={"sbp-avatar mini sbp-mini" + (account === "org" ? " org" : "")} title={account === "org" ? "OpenMake" : "Jay Farei"}>
          {account === "org"
            ? <span className="sbp-face o" style={{ opacity: 1 }}><span className="om-sq" /></span>
            : <img className="sbp-face p" src="assets/avatar-jf.png" alt="" />}
        </span>
      )}

      {!collapsed && account === "org" && membersOpen ? (
        <SbpMembersPanel onClose={() => setMembersOpen(false)} onNav={onNav} />
      ) : (
      <>
      <div className="sb-global-search">
        <button type="button" className="sb-search-input" onClick={onActivateSearch} title="Search everything (⌘K)">
          <Icon name="search" size={13} className="sicon" />
          <span className="sb-search-ph">Search traces, evidence…</span>
          <kbd className="sb-kbd">⌘K</kbd>
        </button>
      </div>

      <nav className="sb-nav" aria-label="Workspace">
        <Sbv2Item id="traces" icon="grid" label="Overview" activeNav={activeNav} onNav={onNav} />
        <Sbv2Item id="traces-index" icon="activity" label="Traces" tech="trace" activeNav={activeNav} onNav={onNav} />
        <Sbv2Item id="boxes" icon="box" label="Boxes & AI" tech="usage" activeNav={activeNav} onNav={onNav} />
        <Sbv2Item id="spotlight" icon="sparkles" label="Spotlight" tech="trace query" activeNav={activeNav} onNav={onNav} />
        <Sbv2Item id="alerts" icon="bell" label="Alerts" activeNav={activeNav} onNav={onNav} />
        <Sbv2Item id="settings" icon="settings" label="Settings" activeNav={activeNav} onNav={onNav} />
      </nav>

      <div className="sb-tree">
        <div className="sb-nav-split" role="separator"></div>
        <Sbv2Repos
          collapsed={collapsed}
          onViewAll={() => onNav("projects-index")}
          expandedRepo={expandedRepo}
          onExpandRepo={onExpandRepo}
          activeRepo={activeRepo}
          activeRepoChild={activeRepoChild}
          onSelectRepoChild={onSelectRepoChild}
        />
        <Sbv2Projections
          collapsed={collapsed}
          onNav={onNav}
          onOpenDataset={(id) => onSelectDatasetChild(id, "overview")}
          activeDatasetId={activeDatasetId}
          onOpenBench={(repoId) => onSelectRepoChild(repoId, "bench")}
          activeRepo={activeRepo}
          activeRepoChild={activeRepoChild}
          onOpenCapsule={onOpenCapsule}
          activeCapsuleId={activeCapsuleId}
        />
        <ArtifactsSection
          collapsed={collapsed}
          onViewAll={() => onNav("artifacts-index")}
          onSelectArtifact={onSelectArtifact}
          activeArtifactId={activeArtifactId}
        />
      </div>
      </>
      )}
    </aside>
  );
}

window.SidebarV2 = SidebarV2;
window.AccountDeck = AccountDeck;
