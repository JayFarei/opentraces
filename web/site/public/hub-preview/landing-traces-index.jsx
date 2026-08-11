// Traces index — full-screen ledger at Jayfarei / Traces.
// Built for recall: filter by known entities (project / dataset / harness),
// scrub the day timeline to revisit "what happened that day", and paginate
// through the full history. The Overview keeps only a widget of this.

const TIX_PER_PAGE = 25;
const TIX_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function TixCheck() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12"></polyline>
    </svg>
  );
}

// Entity filter chip with a popover of known values (reuses .bc-menu glass).
function TixFilter({ label, allLabel, value, options, onChange }) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!open) return;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onEsc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);
  const active = options.find((o) => o.id === value);
  return (
    <span className="tix-filter" ref={ref}>
      <button
        className={"tix-chip" + (value ? " on" : "")}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="tc-k">{label}</span>
        <span className={"tc-v" + (active && active.mono ? " mono" : "")}>{active ? active.label : "All"}</span>
        <Icon name="chevron-down" size={11} className="tc-chev" />
      </button>
      {open && (
        <div className="bc-menu tix-menu" role="menu">
          <div className="bc-menu-group">
            <button className="bc-menu-item" role="menuitemradio" aria-current={!value || undefined} onClick={() => { onChange(null); setOpen(false); }}>
              <span className="mi-label">{allLabel}</span>
              <span className="mi-right">{!value && <span className="mi-check"><TixCheck /></span>}</span>
            </button>
          </div>
          <div className="bc-menu-group">
            {options.map((o) => (
              <button
                key={o.id}
                className="bc-menu-item"
                role="menuitemradio"
                aria-current={value === o.id || undefined}
                onClick={() => { onChange(o.id); setOpen(false); }}
              >
                {o.icon && <Icon name={o.icon} size={13} className="mi-icon" />}
                <span className={"mi-label" + (o.mono ? " mono" : "")}>
                  {o.sub && <span className="mi-sub">{o.sub}</span>}
                  {o.label}
                </span>
                <span className="mi-right">
                  <span className="mi-meta">{o.count}</span>
                  {value === o.id && <span className="mi-check"><TixCheck /></span>}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </span>
  );
}

// Day-by-day activity strip. Click a day to pin the ledger to it; bars
// reflect the current entity filters so you can see WHEN a project was hot.
// Each bar is composed from that day's per-project shares using the same
// hard-stop weave logic (and fill tweak) as the contribution heatmap.
function TixTimeline({ days, selectedKey, onSelect, showLegend }) {
  const max = React.useMemo(() => Math.max(1, ...days.map((d) => d.count)), [days]);
  const hmv = window.useHmView ? window.useHmView() : { fill: "weave" };

  const barStyle = (d, idx) => {
    if (!d.count) return undefined;
    const palette = window.REPO_COLORS || {};
    const ents = Object.entries(d.repos || {}).sort((a, b) => b[1] - a[1]);
    if (!ents.length) return { background: "color-mix(in oklch, var(--c-git) 72%, var(--surface))" };
    const total = ents.reduce((a, e) => a + e[1], 0);
    const cols = ents.map(([r, n]) => ({
      c: `color-mix(in oklch, ${palette[r] || "var(--c-git)"} 76%, var(--surface))`,
      f: n / total,
    }));
    if (cols.length === 1 || hmv.fill === "solo") return { background: cols[0].c };
    let acc = 0;
    const stops = cols.map(({ c, f }) => {
      const from = acc * 100; acc += f;
      return `${c} ${from.toFixed(1)}% ${(acc * 100).toFixed(1)}%`;
    }).join(", ");
    if (hmv.fill === "bands")  return { background: `linear-gradient(180deg, ${stops})` };
    if (hmv.fill === "radial") return { background: `conic-gradient(from ${(idx * 47) % 360}deg, ${stops})` };
    return { background: `linear-gradient(${idx % 2 ? 135 : 45}deg, ${stops})` };
  };

  // Axis ticks at week starts (Mondays), labeled with the date number;
  // the month name is prepended whenever a tick opens a new month.
  const axisTicks = React.useMemo(() => {
    const ticks = [];
    let lastMonth = -1;
    days.forEach((d, i) => {
      if (d.date.getUTCDay() !== 1) return; // Mondays only
      const m = d.date.getUTCMonth();
      const dom = d.date.getUTCDate();
      ticks.push({ i, label: m !== lastMonth ? `${TIX_MONTHS[m]} ${dom}` : String(dom) });
      lastMonth = m;
    });
    // Thin out if the window is long enough that weekly ticks would crowd.
    const minGap = Math.ceil(days.length / 18);
    let lastKept = -Infinity;
    return ticks.filter((t) => {
      if (t.i - lastKept < minGap) return false;
      lastKept = t.i;
      return true;
    });
  }, [days]);
  const sel = selectedKey ? days.find((d) => d.key === selectedKey) : null;
  return (
    <section className="tix-strip-card">
      <header className="tix-strip-head">
        <div className="tix-strip-title">Timeline</div>
        <div className="tix-strip-sub">
          {sel
            ? <>{dayKey(sel.date)} · <span className="mono">{sel.count}</span> {sel.count === 1 ? "trace" : "traces"} <button className="tix-strip-clear" onClick={() => onSelect(null)}>Clear</button></>
            : <>{days.length} days · click a day to pin it</>}
        </div>
      </header>
      <div className="tix-days" role="listbox" aria-label="Traces per day">
        {days.map((d, i) => (
          <button
            key={d.key}
            className={"tix-day" + (d.count === 0 ? " empty" : "") + (selectedKey === d.key ? " sel" : "")}
            role="option"
            aria-selected={selectedKey === d.key}
            title={`${dayKey(d.date)} · ${d.count} ${d.count === 1 ? "trace" : "traces"}`}
            onClick={() => onSelect(selectedKey === d.key ? null : d.key)}
          >
            <span
              className="tix-bar"
              style={{
                height: d.count ? Math.max(6, Math.round((d.count / max) * 40)) + "px" : "3px",
                ...(barStyle(d, i) || {}),
              }}
            ></span>
          </button>
        ))}
      </div>
      <div className="tix-axis">
        {axisTicks.map((t) => (
          <span key={t.i} className="tix-tick" style={{ left: ((t.i + 0.5) / days.length) * 100 + "%" }}>
            <span className="tix-tick-mark"></span>
            <span className="tix-tick-label mono">{t.label}</span>
          </span>
        ))}
      </div>
      {showLegend && (
        <div className="hm-legend tix-legend">
          {Object.entries(window.REPO_COLORS || {}).map(([r, c]) => (
            <span key={r} className="hm-leg-item">
              <span className="hm-leg-dot" style={{ background: c }}></span>
              <span>{r}</span>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function TixPager({ page, nPages, total, from, to, onPage }) {
  if (nPages <= 1) return null;
  const list = [];
  for (let i = 0; i < nPages; i++) {
    if (nPages <= 9 || i === 0 || i === nPages - 1 || Math.abs(i - page) <= 2) list.push(i);
    else if (list[list.length - 1] !== "…") list.push("…");
  }
  return (
    <footer className="tix-pager">
      <div className="pg-info">Showing <span className="mono">{from}–{to}</span> of <span className="mono">{total}</span> traces</div>
      <div className="tix-pg-btns">
        <button className="tix-pg" disabled={page === 0} onClick={() => onPage(page - 1)} aria-label="Previous page">
          <Icon name="chevron-left" size={13} />
        </button>
        {list.map((p, i) =>
          p === "…"
            ? <span key={"e" + i} className="tix-pg-ellipsis">…</span>
            : <button key={p} className={"tix-pg mono" + (p === page ? " on" : "")} onClick={() => onPage(p)}>{p + 1}</button>
        )}
        <button className="tix-pg" disabled={page === nPages - 1} onClick={() => onPage(page + 1)} aria-label="Next page">
          <Icon name="chevron-right" size={13} />
        </button>
      </div>
    </footer>
  );
}

function TracesIndexPage({ onSelectTrace, onCompare }) {
  const ALL = window.ALL_TRACES || window.RECENT_TRACES || [];
  const [fRepo, setFRepo] = React.useState(null);
  const [fDs, setFDs] = React.useState(null);
  const [fAgent, setFAgent] = React.useState(null);
  const [fDay, setFDay] = React.useState(null);
  const [groupBy, setGroupBy] = React.useState("day");
  const [page, setPage] = React.useState(0);

  const ymd = (d) => d.toISOString().slice(0, 10);

  // Known-entity option lists with counts across the full ledger.
  const opts = React.useMemo(() => {
    const rep = {}, ds = {}, ag = {};
    ALL.forEach((t) => {
      rep[t.repo] = (rep[t.repo] || 0) + 1;
      if (t.dataset) ds[t.dataset] = (ds[t.dataset] || 0) + 1;
      ag[t.agent.name] = (ag[t.agent.name] || 0) + 1;
    });
    return {
      repos: Object.entries(rep).sort((a, b) => b[1] - a[1]).map(([id, count]) => ({
        id, count, icon: "git-branch", sub: id.split("/")[0] + "/", label: id.split("/")[1],
      })),
      datasets: Object.entries(ds).sort((a, b) => b[1] - a[1]).map(([id, count]) => ({
        id, count, icon: "datasets", label: id, mono: true,
      })),
      agents: Object.entries(ag).sort((a, b) => b[1] - a[1]).map(([id, count]) => ({
        id, count, icon: "bot", label: id,
      })),
    };
  }, [ALL]);

  const entFiltered = React.useMemo(() => ALL.filter((t) =>
    (!fRepo || t.repo === fRepo) &&
    (!fDs || t.dataset === fDs) &&
    (!fAgent || t.agent.name === fAgent)
  ), [ALL, fRepo, fDs, fAgent]);

  // Full-window day histogram (entity filters applied, day pin not),
  // carrying per-project counts so the bars can weave project colors.
  const days = React.useMemo(() => {
    if (!ALL.length) return [];
    const byDay = new Map();
    entFiltered.forEach((t) => {
      const k = ymd(t.timestamp);
      if (!byDay.has(k)) byDay.set(k, { count: 0, repos: {} });
      const e = byDay.get(k);
      e.count += 1;
      e.repos[t.repo] = (e.repos[t.repo] || 0) + 1;
    });
    const oldest = ALL[ALL.length - 1].timestamp;
    const newest = ALL[0].timestamp;
    const out = [];
    for (
      let ts = Date.UTC(oldest.getUTCFullYear(), oldest.getUTCMonth(), oldest.getUTCDate());
      ts <= newest.getTime();
      ts += 86400000
    ) {
      const date = new Date(ts);
      const e = byDay.get(ymd(date));
      out.push({ key: ymd(date), date, count: e ? e.count : 0, repos: e ? e.repos : {} });
    }
    return out;
  }, [entFiltered, ALL]);

  const filtered = React.useMemo(
    () => (fDay ? entFiltered.filter((t) => ymd(t.timestamp) === fDay) : entFiltered),
    [entFiltered, fDay]
  );

  const filterSig = [fRepo, fDs, fAgent, fDay].join("|");
  React.useEffect(() => setPage(0), [filterSig]);

  const nPages = Math.max(1, Math.ceil(filtered.length / TIX_PER_PAGE));
  const cur = Math.min(page, nPages - 1);
  const pageItems = filtered.slice(cur * TIX_PER_PAGE, cur * TIX_PER_PAGE + TIX_PER_PAGE);
  const anyFilter = fRepo || fDs || fAgent || fDay;

  const clearAll = () => { setFRepo(null); setFDs(null); setFAgent(null); setFDay(null); };

  return (
    <div className="landing landing-traces-index">
      <PageHero
        kicker="Workspace"
        title="Traces"
        subtitle="Every trace across your projects and datasets."
      />
      <div className="ov-dense">
        <TixTimeline days={days} selectedKey={fDay} onSelect={setFDay} showLegend={!fRepo} />

        <div className="tix-toolbar">
          <TixFilter label="Project" allLabel="All projects" value={fRepo} options={opts.repos} onChange={setFRepo} />
          <TixFilter label="Dataset" allLabel="All datasets" value={fDs} options={opts.datasets} onChange={setFDs} />
          <TixFilter label="Harness" allLabel="All harnesses" value={fAgent} options={opts.agents} onChange={setFAgent} />
          {anyFilter && (
            <button className="tix-reset" onClick={clearAll}>
              <Icon name="x" size={11} />
              <span>Reset</span>
            </button>
          )}
          <span className="tix-toolbar-spacer"></span>
          {onCompare && (
            <button className="cmp-entry" onClick={() => onCompare()} title="Compare two runs side by side">
              <span className="ce-ab">A/B</span>
              <span>Compare runs</span>
            </button>
          )}
          <div className="hm-toggle" role="tablist">
            <button className={"hmt " + (groupBy === "day"     ? "on" : "")} onClick={() => setGroupBy("day")}>By day</button>
            <button className={"hmt " + (groupBy === "repo"    ? "on" : "")} onClick={() => setGroupBy("repo")}>By project</button>
            <button className={"hmt " + (groupBy === "dataset" ? "on" : "")} onClick={() => setGroupBy("dataset")}>By dataset</button>
          </div>
        </div>

        {pageItems.length > 0 ? (
          <TracesTable traces={pageItems} groupBy={groupBy} onSelect={onSelectTrace} hideRepo={!!fRepo} />
        ) : (
          <div className="tix-empty">
            <div className="tix-empty-t">No traces match these filters.</div>
            <button className="tix-reset lone" onClick={clearAll}>
              <Icon name="x" size={11} />
              <span>Reset filters</span>
            </button>
          </div>
        )}

        <TixPager
          page={cur}
          nPages={nPages}
          total={filtered.length}
          from={filtered.length === 0 ? 0 : cur * TIX_PER_PAGE + 1}
          to={Math.min(filtered.length, (cur + 1) * TIX_PER_PAGE)}
          onPage={setPage}
        />
      </div>
    </div>
  );
}

window.TracesIndexPage = TracesIndexPage;
