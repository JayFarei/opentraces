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

      <AvatarChat />
    </header>
  );
}

// Easter egg: hovering or clicking Jay's avatar opens a little iMessage-style
// chat where he "types" a note inviting you to reach out. Self-contained — no
// props, no global state. Once open it stays out (it no longer dismisses on
// mouse-leave); clicking away closes it instantly, scrolling away closes it
// after a short timeout.
function AvatarChat() {
  const [open, setOpen] = React.useState(false);
  // How many of the three messages have landed (0–3), and whether the typing
  // indicator is currently showing while Jay "composes" the next one.
  const [revealed, setRevealed] = React.useState(0);
  const [typing, setTyping] = React.useState(true);
  const scrollTimer = React.useRef(null);
  const seqTimers = React.useRef([]);
  const wrapRef = React.useRef(null);

  const clearSeq = () => {
    seqTimers.current.forEach(clearTimeout);
    seqTimers.current = [];
  };
  const clearTimers = () => {
    if (scrollTimer.current) { clearTimeout(scrollTimer.current); scrollTimer.current = null; }
    clearSeq();
  };

  const show = () => {
    if (scrollTimer.current) { clearTimeout(scrollTimer.current); scrollTimer.current = null; }
    if (open) return;
    setRevealed(0);
    setTyping(true);
    setOpen(true);
    // Composing rhythm: a short "typing" beat, then a message lands — three times.
    const at = (ms, fn) => seqTimers.current.push(setTimeout(fn, ms));
    at(820,  () => { setRevealed(1); setTyping(true); });
    at(1500, () => setRevealed(2));   // typing stays on between #1 and #2
    at(1480, () => setTyping(true));
    at(2240, () => { setRevealed(3); setTyping(false); });
  };

  const hide = () => {
    clearTimers();
    setOpen(false);
  };

  // While open: click anywhere outside the popup closes it instantly; scrolling
  // away closes it after a short grace timeout (capture catches the scroll
  // container too, since scroll events don't bubble).
  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) hide();
    };
    const onScroll = () => {
      if (scrollTimer.current) return;
      scrollTimer.current = setTimeout(() => { scrollTimer.current = null; hide(); }, 600);
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  React.useEffect(() => () => clearTimers(), []);

  // still composing the final bubble?
  const composing = typing && revealed < 3;

  return (
    <div
      className="tb-avatar-wrap"
      ref={wrapRef}
      onMouseEnter={show}
      onFocus={show}
    >
      <button className="tb-avatar" aria-label="Account · Jay Farei" title="Jay Farei" onClick={show}>
        <img src="assets/avatar-jf.png" alt="Jay Farei" width="32" height="32" draggable="false" />
      </button>

      {open && (
        <div className="avatar-chat" role="dialog" aria-label="A note from Jay">
          <div className="avatar-chat-tail" aria-hidden="true" />
          <div className="avatar-chat-head">
            <img src="assets/avatar-jf.png" alt="" width="22" height="22" draggable="false" />
            <span className="avatar-chat-name">Jay Farei</span>
            <span className="avatar-chat-now">now</span>
          </div>

          <div className="avatar-chat-thread">
            {revealed >= 1 && (
              <div className="avatar-chat-bubble">hey there, want to chat?</div>
            )}
            {revealed >= 2 && (
              <div className="avatar-chat-bubble">
                drop me a text <span className="avatar-chat-wave">👇</span>
              </div>
            )}
            {revealed >= 3 && (
              <a
                className="avatar-chat-link avatar-chat-bubble-in"
                href="https://x.com/jayfarei"
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
              >
                <svg className="avatar-chat-xmark" viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">
                  <path fill="currentColor" d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24h-6.66l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                </svg>
                @jayfarei
              </a>
            )}
            {composing && (
              <div className="avatar-chat-bubble avatar-chat-bubble-typing">
                <span className="avatar-chat-typing" aria-label="Jay is typing">
                  <span /><span /><span />
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

window.Topbar = Topbar;
