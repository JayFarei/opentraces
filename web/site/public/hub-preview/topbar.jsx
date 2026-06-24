// Topbar — back arrow, breadcrumb, search, notifications, user

// ── Easter egg: Jay's avatar "texts" you (iMessage-styled) ──
// Hover or click opens; sticky once open (mouse-leave does NOT close).
// Click anywhere outside → closes instantly (capture-phase pointerdown).
// Scroll → closes after a ~600ms timeout (one pending timer, not re-queued).
function AvatarChat() {
  const [open, setOpen] = React.useState(false);
  const [stage, setStage] = React.useState(0);
  const wrapRef = React.useRef(null);
  const openRef = React.useRef(false);
  const msgTimers = React.useRef([]);
  const scrollTimer = React.useRef(null);

  const clearTimers = () => {
    msgTimers.current.forEach(clearTimeout);
    msgTimers.current = [];
    if (scrollTimer.current) { clearTimeout(scrollTimer.current); scrollTimer.current = null; }
  };

  const hide = () => {
    if (!openRef.current) return;
    openRef.current = false;
    clearTimers();
    setOpen(false);
  };

  const show = () => {
    if (openRef.current) return;
    openRef.current = true;
    clearTimers();
    setOpen(true);
    setStage(0);
    // typing indicator shows immediately; messages land on a composing rhythm
    msgTimers.current = [
      setTimeout(() => setStage(1), 820),
      setTimeout(() => setStage(2), 1500),
      setTimeout(() => setStage(3), 2240),
    ];
  };

  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) hide();
    };
    const onScroll = () => {
      if (scrollTimer.current) return; // one pending timer, never re-queued
      scrollTimer.current = setTimeout(() => { scrollTimer.current = null; hide(); }, 600);
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  React.useEffect(() => clearTimers, []);

  return (
    <div className="tb-avatar-wrap" ref={wrapRef}>
      <button
        className="tb-avatar"
        aria-label="Account · Jay Farei"
        title="Jay Farei"
        onMouseEnter={show}
        onClick={show}
        onFocus={show}
      >
        <img src="assets/avatar-jf.png" alt="Jay Farei" width="32" height="32" draggable="false" />
      </button>
      {open && (
        <div className="avatar-chat" role="dialog" aria-label="Message from Jay Farei">
          <div className="avatar-chat-tail"></div>
          <div className="avatar-chat-head">
            <img src="assets/avatar-jf.png" alt="" draggable="false" />
            <span className="avatar-chat-name">Jay Farei</span>
            <span className="avatar-chat-now">now</span>
          </div>
          <div className="avatar-chat-thread">
            {stage >= 1 && <div className="avatar-chat-bubble">hey there, want to chat?</div>}
            {stage >= 2 && <div className="avatar-chat-bubble">drop me a text <span className="avatar-chat-wave">👇</span></div>}
            {stage >= 3 && (
              <a className="avatar-chat-link" href="https://x.com/jayfarei" target="_blank" rel="noopener noreferrer">
                <svg className="avatar-chat-xmark" width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24h-6.66l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"></path>
                </svg>
                @jayfarei
              </a>
            )}
            {stage < 3 && (
              <div className="avatar-chat-bubble avatar-chat-bubble-typing">
                <span className="avatar-chat-typing"><span></span><span></span><span></span></span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Topbar({ workspace, project, traceShortId, onBack, theme, onToggleTheme, view, contextLabel }) {
  const isLanding = view !== "trace";
  const showBack = view === "trace" || view === "compare";
  const PAGE_LABELS = {
    intents:   { group: "Intelligence", page: "Intents" },
    evals:     { group: "Intelligence", page: "Evals" },
    spotlight: { group: "Intelligence", page: "Spotlight" },
    capsules:  { group: "Intelligence", page: "Capsules" },
    alerts:    { group: "Intelligence", page: "Alerts" },
    improving: { group: "Intelligence", page: "Self-Improving" },
  };
  const pg = PAGE_LABELS[view];
  return (
    <header className="topbar">
      {showBack && (
        <button className="tb-back" onClick={onBack} aria-label="Back">
          <Icon name="back" size={16} />
        </button>
      )}
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
        {pg && <><span className="bc-sep">/</span><button className="bc-item">{pg.group}</button><span className="bc-sep">/</span><span className="bc-item current">{pg.page}</span></>}
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

      <AvatarChat />
    </header>
  );
}

window.Topbar = Topbar;
