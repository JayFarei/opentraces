// Command palette — global ⌘K search across the whole app.
// Two modes: "all" (pages, projects, datasets, traces) and "spotlight"
// (traces only — Tab toggles between them). Keyboard-first.

const CMD_PAGES = [
  { id: "traces",    label: "Overview",       hint: "All traces ledger",        icon: "grid",        kind: "nav" },
  { id: "spotlight", label: "Spotlight",      hint: "AI-graded trace search",   icon: "sparkles",    kind: "nav" },
  { id: "boxes",     label: "Boxes & AI",     hint: "Box leases & AI credit usage", icon: "box",  kind: "nav" },
  { id: "capsules",  label: "Capsules",       hint: "Sealed runs that travel — witness-first", icon: "capsule", kind: "nav" },
  { id: "alerts",    label: "Alerts",         hint: "Triggers & notifications", icon: "bell",        kind: "nav" },
  { id: "compare",   label: "Compare traces", hint: "Side-by-side diff",        icon: "trail",       kind: "compare" },
];

function CmdRow({ item, idx, active, onHover, onRun }) {
  return (
    <button
      type="button"
      className="cmd-row"
      data-idx={idx}
      data-active={active ? "true" : "false"}
      onMouseMove={() => onHover(idx)}
      onClick={() => onRun(item)}
    >
      {item.render
        ? item.render()
        : (
          <>
            <span className="cmd-row-ico"><Icon name={item.icon} size={15} /></span>
            <span className="cmd-row-main">
              <span className="cmd-row-title">{item.title}</span>
              {item.sub && <span className="cmd-row-sub">{item.sub}</span>}
            </span>
            {item.meta && <span className="cmd-row-meta mono">{item.meta}</span>}
          </>
        )}
      <span className="cmd-row-enter" aria-hidden="true">↵</span>
    </button>
  );
}

function CommandPaletteV2({ open, onClose, actions }) {
  const [query, setQuery] = React.useState("");
  const [mode, setMode] = React.useState("all"); // 'all' | 'spotlight'
  const [active, setActive] = React.useState(0);
  const inputRef = React.useRef(null);
  const listRef = React.useRef(null);

  React.useEffect(() => {
    if (open) {
      setQuery("");
      setMode("all");
      setActive(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const repos = React.useMemo(
    () => Object.entries(window.REPO_DEFS || {}).map(([id, d]) => ({ id, ...d })), []);
  const datasets = React.useMemo(
    () => Object.entries(window.DATASET_DEFS || {}).map(([id, d]) => ({ id, ...d })), []);
  const traces = window.RECENT_TRACES || [];

  const q = query.trim().toLowerCase();
  const hit = (...fields) => !q || fields.filter(Boolean).join(" ").toLowerCase().includes(q);

  // Build the grouped result set.
  const groups = React.useMemo(() => {
    const traceItem = (t) => ({
      key: "trace:" + t.id,
      kind: "trace",
      run: () => actions.openTrace(t.id),
      render: () => (
        <>
          <span className="cmd-row-fp"><Fingerprint classes={t.fingerprint} height={16} gap={1} /></span>
          <span className="cmd-row-main">
            <span className="cmd-row-title">{t.title}</span>
            <span className="cmd-row-sub">
              <AgentGlyph agent={t.agent} size={9} />
              <span>{t.agent.name}</span>
              <span className="cmd-dot" />
              <span className="mono">{t.repo}</span>
            </span>
          </span>
          <span className="cmd-row-meta mono">{t.id}</span>
        </>
      ),
    });

    if (mode === "spotlight") {
      const out = [];
      const matched = traces.filter(t => hit(t.title, t.repo, t.agent.name, t.id));
      if (q) {
        out.push({
          label: null,
          items: [{
            key: "spotlight-run",
            kind: "spotlight",
            run: () => actions.runSpotlight(query.trim()),
            render: () => (
              <>
                <span className="cmd-row-ico accent"><Icon name="sparkles" size={15} /></span>
                <span className="cmd-row-main">
                  <span className="cmd-row-title">Ask Spotlight — “{query.trim()}”</span>
                  <span className="cmd-row-sub">AI-graded search across bodies, reasoning &amp; tool calls</span>
                </span>
                <span className="cmd-row-meta">Beta</span>
              </>
            ),
          }],
        });
      }
      out.push({
        label: q ? `Matching traces · ${matched.length}` : "Recent traces",
        items: (q ? matched : traces).slice(0, 20).map(traceItem),
      });
      return out;
    }

    // mode === "all"
    const out = [];

    const pageItems = CMD_PAGES
      .filter(p => hit(p.label, p.hint))
      .map(p => ({
        key: "page:" + p.id,
        kind: p.kind,
        icon: p.icon,
        title: p.label,
        sub: p.hint,
        run: () => p.kind === "compare" ? actions.openCompare() : actions.navPage(p.id),
      }));
    if (pageItems.length) out.push({ label: "Jump to", items: pageItems });

    const repoItems = repos
      .filter(r => hit(r.ns + "/" + r.nm, r.description))
      .map(r => ({
        key: "repo:" + r.id,
        kind: "repo",
        icon: "git-branch",
        title: r.ns + "/" + r.nm,
        sub: r.description,
        meta: (r.open_traces ? r.open_traces + " open" : ""),
        run: () => actions.openRepo(r.id),
      }));
    if (repoItems.length) out.push({ label: "Projects", items: repoItems });

    const dsItems = datasets
      .filter(d => hit(d.name, d.description))
      .map(d => ({
        key: "ds:" + d.id,
        kind: "dataset",
        icon: "datasets",
        title: d.name,
        sub: d.description,
        meta: d.rows ? d.rows + " rows" : "",
        run: () => actions.openDataset(d.id),
      }));
    if (dsItems.length) out.push({ label: "Datasets", items: dsItems });

    const tHits = traces.filter(t => hit(t.title, t.repo, t.agent.name, t.id));
    const tItems = (q ? tHits : traces.slice(0, 6)).slice(0, q ? 8 : 6).map(t => ({
      key: "trace:" + t.id,
      kind: "trace",
      run: () => actions.openTrace(t.id),
      render: () => (
        <>
          <span className="cmd-row-fp"><Fingerprint classes={t.fingerprint} height={16} gap={1} /></span>
          <span className="cmd-row-main">
            <span className="cmd-row-title">{t.title}</span>
            <span className="cmd-row-sub">
              <AgentGlyph agent={t.agent} size={9} />
              <span>{t.agent.name}</span>
              <span className="cmd-dot" />
              <span className="mono">{t.repo}</span>
            </span>
          </span>
          <span className="cmd-row-meta mono">{t.id}</span>
        </>
      ),
    }));
    if (tItems.length) out.push({ label: q ? `Traces · ${tHits.length}` : "Recent traces", items: tItems });

    return out;
  }, [mode, q, query, repos, datasets, traces]);

  // Flatten for keyboard navigation; tag each item with its global index.
  const flat = React.useMemo(() => groups.flatMap(g => g.items), [groups]);

  React.useEffect(() => {
    setActive(a => Math.min(Math.max(0, a), Math.max(0, flat.length - 1)));
  }, [flat.length]);

  // Keep the active row visible without scrollIntoView (which can jump the page).
  React.useEffect(() => {
    const c = listRef.current;
    if (!c) return;
    const el = c.querySelector(`[data-idx="${active}"]`);
    if (!el) return;
    const top = el.offsetTop;
    const bottom = top + el.offsetHeight;
    if (top < c.scrollTop) c.scrollTop = top - 8;
    else if (bottom > c.scrollTop + c.clientHeight) c.scrollTop = bottom - c.clientHeight + 8;
  }, [active]);

  const exec = (item) => {
    if (!item) return;
    onClose();
    item.run();
  };

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(a => Math.min(a + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); exec(flat[active]); }
    else if (e.key === "Tab") { e.preventDefault(); setMode(m => m === "all" ? "spotlight" : "all"); setActive(0); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };

  if (!open) return null;

  let runner = -1; // running global index across groups

  return (
    <div className="cmdk-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Command palette">
        <div className="cmdk-input-row">
          <Icon name={mode === "spotlight" ? "sparkles" : "search"} size={16} className="cmdk-input-ico" />
          <input
            ref={inputRef}
            className="cmdk-input"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder={mode === "spotlight" ? "Search traces…" : "Search traces, projects, datasets, pages…"}
            spellCheck={false}
          />
          <button
            type="button"
            className="cmdk-mode"
            data-mode={mode}
            onClick={() => { setMode(m => m === "all" ? "spotlight" : "all"); setActive(0); inputRef.current?.focus(); }}
            title="Toggle Spotlight (Tab)"
          >
            {mode === "spotlight" ? "Spotlight" : "All"}
            <kbd className="cmdk-tab">Tab</kbd>
          </button>
        </div>

        <div className="cmdk-list" ref={listRef}>
          {flat.length === 0 && (
            <div className="cmdk-empty">No matches for “{query.trim()}”</div>
          )}
          {groups.map((g, gi) => (
            <div className="cmdk-group" key={gi}>
              {g.label && <div className="cmdk-group-label">{g.label}</div>}
              {g.items.map((item) => {
                runner += 1;
                const idx = runner;
                return (
                  <CmdRow
                    key={item.key}
                    item={item}
                    idx={idx}
                    active={idx === active}
                    onHover={setActive}
                    onRun={exec}
                  />
                );
              })}
            </div>
          ))}
        </div>

        <div className="cmdk-foot">
          <span className="cmdk-hint"><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span className="cmdk-hint"><kbd>↵</kbd> open</span>
          <span className="cmdk-hint"><kbd>Tab</kbd> {mode === "all" ? "Spotlight" : "All"}</span>
          <span className="cmdk-hint"><kbd>Esc</kbd> close</span>
          {mode === "spotlight" && <span className="cmdk-foot-note">Traces only</span>}
        </div>
      </div>
    </div>
  );
}

window.CommandPaletteV2 = CommandPaletteV2;
