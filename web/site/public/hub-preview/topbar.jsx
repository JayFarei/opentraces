// Topbar — back arrow, breadcrumb, search, notifications, user

function Topbar({ workspace, project, traceShortId, onBack, theme, onToggleTheme, view, contextLabel }) {
  const isLanding = view !== "trace";
  const showBack = view === "trace" || view === "compare";
  const PAGE_LABELS = {
    intents:   { group: "Intelligence", page: "Intents" },
    evals:     { group: "Intelligence", page: "Evals" },
    spotlight: { group: "Intelligence", page: "Spotlight" },
    capsules:  { group: "Intelligence", page: "Capsules" },
    alerts:    { group: "Intelligence", page: "Alerts" },
    improving: { group: "Automation",   page: "Self-Improving" },
  };
  const pg = PAGE_LABELS[view];
  return (
    <header className="topbar">
      <button className="tb-back" onClick={onBack} aria-label="Back" style={{ visibility: showBack ? "visible" : "hidden" }}>
        <Icon name="back" size={16} />
      </button>
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <img
          className="bc-hf-logo"
          src="assets/hf-logo-mono.svg"
          alt="Hugging Face"
          width="30"
          height="30"
          draggable="false"
        />
        <span className="bc-brand-div" aria-hidden="true" />
        <button className="bc-item">{workspace}</button>
        {view === "trace" && <><span className="bc-sep">/</span><button className="bc-item">{project}</button><span className="bc-sep">/</span><button className="bc-item">Traces</button><span className="bc-sep">/</span><span className="bc-item current mono">{traceShortId}</span></>}
        {view === "traces-landing" && <><span className="bc-sep">/</span><span className="bc-item current">Overview</span></>}
        {view === "compare" && <><span className="bc-sep">/</span><button className="bc-item">Traces</button><span className="bc-sep">/</span><span className="bc-item current">Compare</span></>}
        {view === "repo" && <><span className="bc-sep">/</span><span className="bc-item current mono">{contextLabel}</span></>}
        {view === "dataset" && <><span className="bc-sep">/</span><button className="bc-item">Datasets</button><span className="bc-sep">/</span><span className="bc-item current mono">{contextLabel}</span></>}
        {pg && <><span className="bc-sep">/</span><button className="bc-item">{pg.group}{pg.group === "Intelligence" && <span className="pro-badge">PRO</span>}</button><span className="bc-sep">/</span><span className="bc-item current">{pg.page}</span></>}
      </nav>

      <div className="tb-spacer" />

      <button
        className="tb-icon-btn"
        aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
        title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
        onClick={onToggleTheme}
      >
        <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
      </button>

      <button className="tb-avatar" aria-label="Account · Jay Farei" title="Jay Farei">
        <img src="assets/avatar-jf.png" alt="Jay Farei" width="32" height="32" draggable="false" />
      </button>
    </header>
  );
}

window.Topbar = Topbar;
