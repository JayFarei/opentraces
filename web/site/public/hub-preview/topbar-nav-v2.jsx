// TopbarNav — breadcrumb-driven navigation (Liquid).
//
// The breadcrumb IS the router:
//  · Back/forward are history-driven and ALWAYS rendered (disabled when
//    unavailable) so the bar never shifts.
//  · One canonical path grammar:
//      Jayfarei / {Page}                        (Overview, Intelligence pages, Settings)
//      Jayfarei / {project|dataset} / {Tab}     (entities, GitHub owner/name idiom)
//      Jayfarei / Traces / {trace-id}           (trace viewer, Compare)
//  · Every non-leaf segment clicks UP a level; segments with siblings get a
//    switcher (⌄) listing peers so you can move LATERALLY without going up.

const BC_INTEL_PAGES = [
  { id: "spotlight", label: "Spotlight", icon: "sparkles" },
  { id: "capsules",  label: "Capsules",  icon: "capsule" },
  { id: "alerts",    label: "Alerts",    icon: "bell" },
];

const BC_PAGE_VIEWS = {
  "traces-index":   { nav: "traces-index",   label: "Traces" },
  "datasets-index": { nav: "datasets-index", label: "Datasets" },
  "projections-index": { nav: "projections-index", label: "Projections" },
  "projects-index": { nav: "projects-index", label: "Projects" },
  "artifacts-index": { nav: "artifacts-index", label: "Artifacts" },
  spotlight:        { nav: "spotlight", label: "Spotlight" },
  capsules:         { nav: "capsules",  label: "Capsules" },
  alerts:           { nav: "alerts",    label: "Alerts" },
  settings:         { nav: "settings",  label: "Settings" },
  "evidence-detail": { nav: "", label: "Evidence" },
  parameterizer:    { nav: "",  label: "Parameterizer" },
  boxes:            { nav: "boxes",     label: "Boxes & AI" },

};

const BC_REPO_CHILDREN = [
  { id: "overview", label: "Overview" },
  { id: "traces",   label: "Traces" },
  { id: "pulls",    label: "Pull requests" },
  { id: "bench", label: "Bench" },
  { id: "settings", label: "Settings" },
];

const BC_DATASET_CHILDREN = [
  { id: "overview", label: "Overview" },
  { id: "inbox",    label: "Inbox" },
  { id: "workflow", label: "Workflow" },
];

function BcCheck() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12"></polyline>
    </svg>
  );
}

function BcMenu({ menu, onPick, flip, innerRef }) {
  return (
    <div className={"bc-menu" + (menu.wide ? " wide" : "") + (flip ? " flip" : "")} role="menu" ref={innerRef}>
      {menu.groups.map((g, gi) => (
        <div className="bc-menu-group" key={gi}>
          {g.label && <div className="bc-menu-glabel">{g.label}</div>}
          {g.items.map((it) => (
            <button
              key={it.id}
              className="bc-menu-item"
              role="menuitemradio"
              aria-current={it.active || undefined}
              onClick={() => onPick(it)}
            >
              {it.icon && <Icon name={it.icon} size={13} className="mi-icon" />}
              <span className="mi-label">
                {it.sub && <span className="mi-sub">{it.sub}</span>}
                {it.label}
              </span>
              <span className="mi-right">
                {it.meta && <span className="mi-meta">{it.meta}</span>}
                {it.active && <span className="mi-check"><BcCheck /></span>}
              </span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

// One breadcrumb segment. Text click = navigate up (onClick) — or, on a leaf
// with siblings, open the switcher. Segments with BOTH get a split caret.
function BcSeg({ seg, open, onToggle, onPick }) {
  const { label, mono, icon, current, onClick, menu } = seg;
  // Popovers left-align to their segment; segments near the right edge flip
  // to right-aligned so the menu never overflows the viewport.
  const menuRef = React.useRef(null);
  const [flip, setFlip] = React.useState(false);
  React.useLayoutEffect(() => {
    if (!open) { setFlip(false); return; }
    const m = menuRef.current;
    if (!m) return;
    const r = m.getBoundingClientRect();
    if (r.right > window.innerWidth - 12) setFlip(true);
  }, [open]);
  const clsBtn =
    "bc-item" + (current ? " current" : "") + (mono ? " mono" : "") + (menu && !onClick ? " has-menu" : "");
  return (
    <span className="bc-seg" data-open={open ? "true" : "false"}>
      <button
        className={clsBtn}
        aria-haspopup={menu && !onClick ? "menu" : undefined}
        aria-expanded={menu && !onClick ? open : undefined}
        onClick={onClick ? onClick : menu ? onToggle : undefined}
      >
        {icon && <Icon name={icon} size={13} className="bc-seg-icon" />}
        <span className="bc-lbl">{label}</span>
        {menu && !onClick && <Icon name="chevron-down" size={11} className="bc-caret-in" />}
      </button>
      {menu && onClick && (
        <button
          className="bc-caret"
          data-open={open ? "true" : "false"}
          aria-label={`Switch ${label}`}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={onToggle}
        >
          <Icon name="chevron-down" size={11} />
        </button>
      )}
      {open && menu && <BcMenu menu={menu} onPick={onPick} flip={flip} innerRef={menuRef} />}
    </span>
  );
}

function buildCrumbs({ route, actions, workspace, traceShortId }) {
  const crumbs = [];
  const goHome = () => actions.navPage("traces");
  const isHome = route.view === "traces-landing";
  const pg = BC_PAGE_VIEWS[route.view];
  const curSection = isHome ? "traces"
    : route.view === "capsule-detail" ? "capsules"
    : pg ? pg.nav
    : (route.view === "trace" || route.view === "compare") ? "traces-index"
    : route.view === "repo" ? "projects-index"
    : route.view === "dataset" ? "datasets-index"
    : route.view === "artifact" ? "artifacts-index"
    : "";

  // Workspace sections — one switcher, reachable from the root segment (and
  // from the page leaf), so every level of the tree is accessible anywhere.
  const sectionsMenu = {
    groups: [
      {
        items: [
          { id: "traces",         label: "Overview", icon: "grid",       active: curSection === "traces",         go: goHome },
          { id: "traces-index",   label: "Traces",   icon: "activity",   active: curSection === "traces-index",   go: () => actions.navPage("traces-index") },
          { id: "datasets-index", label: "Datasets", icon: "datasets",   active: curSection === "datasets-index", go: () => actions.navPage("datasets-index") },
          { id: "projects-index", label: "Projects", icon: "git-branch", active: curSection === "projects-index", go: () => actions.navPage("projects-index") },
          { id: "artifacts-index", label: "Artifacts", icon: "swatch",   active: curSection === "artifacts-index", go: () => actions.navPage("artifacts-index") },
          { id: "boxes", label: "Boxes & AI", icon: "box", active: curSection === "boxes", go: () => actions.navPage("boxes") },
        ],
      },
      {
        label: "Intelligence",
        items: BC_INTEL_PAGES.map((p) => ({
          id: p.id, label: p.label, icon: p.icon,
          active: curSection === p.id, go: () => actions.navPage(p.id),
        })),
      },
      { items: [{ id: "settings", label: "Settings", icon: "settings", active: curSection === "settings", go: () => actions.navPage("settings") }] },
    ],
  };

  // ── Root: the workspace IS the overview. On home it's the current leaf;
  // elsewhere it clicks home. Either way it carries the sections switcher. ──
  crumbs.push({
    key: "ws",
    label: workspace,
    current: isHome,
    onClick: isHome ? undefined : goHome,
    menu: sectionsMenu,
  });
  if (isHome) return crumbs;

  // ── Workspace-level sections: Jayfarei / {Section}▾ ──
  if (pg) {
    crumbs.push({ key: "page", label: pg.label, current: true, menu: sectionsMenu });
    return crumbs;
  }

  // Shared entity switcher: move across projects AND datasets from one menu.
  // Footer escapes to the full index pages — the menu only lists what's here.
  const entityMenu = (curId) => ({
    groups: [
      {
        label: "Projects",
        items: Object.entries(window.REPO_DEFS || {}).map(([id, d]) => ({
          id, label: d.nm, sub: (d.ns || "").toLowerCase() + "/", icon: "git-branch",
          active: curId === id, go: () => actions.openRepo(id, "overview"),
        })),
      },
      {
        label: "Datasets",
        items: Object.entries(window.DATASET_DEFS || {}).map(([id, d]) => ({
          id, label: d.name, icon: "datasets",
          active: curId === id, go: () => actions.openDataset(id, "overview"),
        })),
      },
      {
        items: [
          { id: "all-projects", label: "All projects…", meta: String(Object.keys(window.REPO_DEFS || {}).length),    go: () => actions.navPage("projects-index") },
          { id: "all-datasets", label: "All datasets…", meta: String(Object.keys(window.DATASET_DEFS || {}).length), go: () => actions.navPage("datasets-index") },
        ],
      },
    ],
  });

  // ── Entities: Jayfarei / Projects / {entity}▾ / {Tab}▾ ──
  if (route.view === "repo" && route.repoId) {
    const def = (window.REPO_DEFS || {})[route.repoId];
    const nm = def ? def.nm : String(route.repoId).split("/").pop();
    const child = route.repoChild || "overview";
    crumbs.push({ key: "sect", label: "Projects", onClick: () => actions.navPage("projects-index"), menu: sectionsMenu });
    crumbs.push({
      key: "entity", label: nm, icon: "git-branch",
      onClick: () => actions.openRepo(route.repoId, "overview"),
      menu: entityMenu(route.repoId),
    });
    crumbs.push({
      key: "tab",
      label: (BC_REPO_CHILDREN.find((c) => c.id === child) || BC_REPO_CHILDREN[0]).label,
      current: !(child === "pulls" && route.pullId),
      onClick: child === "pulls" && route.pullId ? () => actions.openRepo(route.repoId, "pulls") : undefined,
      menu: {
        groups: [{
          items: BC_REPO_CHILDREN.map((c) => ({
            id: c.id, label: c.label,
            active: child === c.id, go: () => actions.openRepo(route.repoId, c.id),
          })),
        }],
      },
    });
    // Open PR detail — the breadcrumb leaf, with a lateral switcher over
    // the repo's pull requests.
    if (child === "pulls" && route.pullId) {
      const pulls = (window.REPO_PULLS || {})[route.repoId] || [];
      const cur = pulls.find((p) => p.id === route.pullId);
      crumbs.push({
        key: "pull",
        label: cur ? "#" + cur.number : route.pullId,
        mono: true,
        current: true,
        menu: {
          wide: true,
          groups: [{
            label: "Pull requests",
            items: pulls.filter((p) => p.detail).map((p) => ({
              id: p.id, label: p.title, sub: "#" + p.number + " ", meta: p.branch,
              active: p.id === route.pullId,
              go: () => actions.openPull && actions.openPull(route.repoId, p.id),
            })),
          }],
        },
      });
    }
    return crumbs;
  }

  if (route.view === "dataset" && route.datasetId) {
    const def = (window.DATASET_DEFS || {})[route.datasetId];
    const nm = def ? def.name : route.datasetId;
    const child = route.datasetChild || "overview";
    crumbs.push({ key: "sect", label: "Datasets", onClick: () => actions.navPage("datasets-index"), menu: sectionsMenu });
    crumbs.push({
      key: "entity", label: nm, icon: "datasets",
      onClick: () => actions.openDataset(route.datasetId, "overview"),
      menu: entityMenu(route.datasetId),
    });
    crumbs.push({
      key: "tab",
      label: (BC_DATASET_CHILDREN.find((c) => c.id === child) || BC_DATASET_CHILDREN[0]).label,
      current: true,
      menu: {
        groups: [{
          items: BC_DATASET_CHILDREN.map((c) => ({
            id: c.id, label: c.label,
            active: child === c.id, go: () => actions.openDataset(route.datasetId, c.id),
          })),
        }],
      },
    });
    return crumbs;
  }

  // ── Capsules: Jayfarei / Capsules / {cid} ──
  if (route.view === "capsule-detail") {
    const cap = (window.CAPSULES || []).find(c => c.id === route.capsuleId);
    crumbs.push({ key: "sect", label: "Capsules", onClick: () => actions.navPage("capsules"), menu: sectionsMenu });
    crumbs.push({ key: "cap", label: cap ? cap.cid : "capsule", current: true });
    return crumbs;
  }

  // ── Traces: Jayfarei / Traces▾ / {id}▾  ·  Jayfarei / Traces / Compare ──
  if (route.view === "trace") {
    crumbs.push({ key: "sect", label: "Traces", onClick: () => actions.navPage("traces-index"), menu: sectionsMenu });
    crumbs.push({
      key: "trace",
      label: traceShortId,
      mono: true,
      current: true,
      menu: {
        wide: true,
        groups: [{
          label: "Recent traces",
          items: (window.RECENT_TRACES || []).slice(0, 8).map((t) => ({
            id: t.id, label: t.title, meta: t.id,
            active: route.traceId === t.id, go: () => actions.openTrace(t.id),
          })),
        }],
      },
    });
    return crumbs;
  }

  if (route.view === "compare") {
    crumbs.push({ key: "sect", label: "Traces", onClick: () => actions.navPage("traces-index"), menu: sectionsMenu });
    crumbs.push({ key: "cmp", label: "Compare", current: true });
    return crumbs;
  }

  // ── Artifacts: Jayfarei / Artifacts / {name}▾ ──
  if (route.view === "artifact" && route.artifactId) {
    const store = window.OtArtifacts;
    const a = store ? store.find(route.artifactId) : null;
    crumbs.push({ key: "sect", label: "Artifacts", onClick: () => actions.navPage("artifacts-index"), menu: sectionsMenu });
    crumbs.push({
      key: "art",
      label: a ? a.name : route.artifactId,
      current: true,
      menu: {
        wide: true,
        groups: [{
          label: "Artifacts",
          items: (store ? store.get() : []).slice(0, 8).map((x) => ({
            id: x.id, label: x.name, meta: x.kind,
            active: x.id === route.artifactId,
            go: () => actions.openArtifact && actions.openArtifact(x.id),
          })),
        }],
      },
    });
    return crumbs;
  }

  return crumbs;
}

function TopbarNavV2({
  workspace, route,
  canBack, canFwd, onHistBack, onHistFwd,
  actions, theme, onToggleTheme, traceShortId,
}) {
  const [openKey, setOpenKey] = React.useState(null);
  const navRef = React.useRef(null);

  // Any route change closes the switcher.
  const routeSig = [route.view, route.nav, route.traceId, route.repoId, route.repoChild, route.pullId, route.datasetId, route.datasetChild].join("|");
  React.useEffect(() => setOpenKey(null), [routeSig]);

  React.useEffect(() => {
    if (!openKey) return;
    const onDown = (e) => {
      if (navRef.current && !navRef.current.contains(e.target)) setOpenKey(null);
    };
    const onEsc = (e) => { if (e.key === "Escape") setOpenKey(null); };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onEsc);
    };
  }, [openKey]);

  const crumbs = buildCrumbs({ route, actions, workspace, traceShortId });

  return (
    <header className="topbar">
      <div className="tb-hist" role="group" aria-label="History">
        <button className="tb-hist-btn" disabled={!canBack} onClick={onHistBack} aria-label="Back" title="Back (⌥←)">
          <Icon name="back" size={15} />
        </button>
        <button className="tb-hist-btn" disabled={!canFwd} onClick={onHistFwd} aria-label="Forward" title="Forward (⌥→)">
          <Icon name="chevron-right" size={15} />
        </button>
      </div>

      <nav className="breadcrumb bc-nav" aria-label="Breadcrumb" ref={navRef}>
        <span className="wm bc-wm">
          <span className="open">open</span>
          <span className="traces">traces</span>
        </span>
        <span className="bc-brand-div" aria-hidden="true"></span>
        {crumbs.map((seg, i) => (
          <React.Fragment key={seg.key}>
            {i > 0 && <span className="bc-sep">/</span>}
            <BcSeg
              seg={seg}
              open={openKey === seg.key}
              onToggle={() => setOpenKey(openKey === seg.key ? null : seg.key)}
              onPick={(it) => { setOpenKey(null); it.go(); }}
            />
          </React.Fragment>
        ))}
      </nav>

      <div className="tb-spacer"></div>

      <button
        className="tb-icon-btn"
        aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
        title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
        data-agent-action="toggle-theme"
        data-agent-desc="Switch between light and dark theme"
        onClick={onToggleTheme}
      >
        <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
      </button>
    </header>
  );
}

window.TopbarNavV2 = TopbarNavV2;
