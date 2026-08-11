// Overview — the numbers, two charts, and the trace ledger. Nothing else.
//
// Layout:
//   1. Stat strip — traces, tokens, projects, datasets, benches, capsules, gyms
//   2. Contribution heatmap (Volume / By project / By dataset)
//   3. Token distribution (By project / By model / By harness / By member on org)
//   4. Ledger table of recent traces
// Stat strip + distribution live in landing-overview-simple.jsx.

const DATASET_COLORS = {
  "claude-eval-v3":   "var(--c-read)",
  "keystrokes-2026":  "var(--c-plan)",
  "edge-traces":      "var(--c-write)",
  "rag-fixtures":     "var(--c-exec)",
  "shortcuts-bench":  "var(--c-user)",
  ...(window.ORG_DATASET_COLORS || {}),
};

const REPO_COLORS = {
  "jayfarei/opentraces":     "var(--c-write)",
  "jayfarei/datafetch":      "var(--c-read)",
  "jayfarei/lazymem":        "var(--c-plan)",
  "jayfarei/clarify":        "var(--c-exec)",
  "jayfarei/open-data":      "var(--c-git)",
  "jayfarei/koreader-kiosk": "var(--c-user)",
  ...(window.ORG_REPO_COLORS || {}),
};

// Shared store for the heatmap cell-fill algorithm — the Tweaks panel writes
// to it, the heatmap reads it. Persisted so the choice survives reloads.
window.HmView = window.HmView || (function () {
  let fill = "weave";
  try { fill = localStorage.getItem("ot-hm-fill") || "weave"; } catch (e) {}
  let state = { fill };
  const subs = new Set();
  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...patch };
      try { localStorage.setItem("ot-hm-fill", state.fill); } catch (e) {}
      subs.forEach((f) => f(state));
    },
    sub(f) { subs.add(f); return () => subs.delete(f); },
  };
})();
function useHmView() {
  const [s, setS] = React.useState(window.HmView.get());
  React.useEffect(() => window.HmView.sub(setS), []);
  return s;
}
window.useHmView = useHmView;

function ContributionHeatmap({ data, mode, onMode, onSelectDay, selectedDay }) {
  const max = React.useMemo(() => {
    let m = 0;
    data.forEach(w => w.forEach(c => { if (c.count > m) m = c.count; }));
    return m;
  }, [data]);

  const hmv = useHmView();

  // Compose a cell background from the day's per-project (or per-dataset)
  // proportions. Hard-stop gradients keep each color's share readable;
  // the per-cell index seeds the orientation so the field feels hand-set
  // rather than mechanical.
  const compose = (ents, palette, intensity, idx) => {
    const totalN = ents.reduce((a, e) => a + e[1], 0) || 1;
    const cols = ents.map(([name, n]) => ({
      c: `color-mix(in oklch, ${palette[name] || "var(--c-git)"} ${25 + intensity * 65}%, var(--surface))`,
      f: n / totalN,
    }));
    if (cols.length === 1 || hmv.fill === "solo") return { background: cols[0].c };
    let acc = 0;
    const stops = cols.map(({ c, f }) => {
      const from = acc * 100; acc += f;
      return `${c} ${from.toFixed(1)}% ${(acc * 100).toFixed(1)}%`;
    }).join(", ");
    if (hmv.fill === "weave")  return { background: `linear-gradient(${idx % 2 ? 135 : 45}deg, ${stops})` };
    if (hmv.fill === "bands")  return { background: `linear-gradient(180deg, ${stops})` };
    if (hmv.fill === "radial") return { background: `conic-gradient(from ${(idx * 47) % 360}deg, ${stops})` };
    return { background: cols[0].c };
  };

  const cellStyle = (cell, idx) => {
    if (cell.count === 0) return { background: "color-mix(in oklch, var(--fg) 6%, transparent)" };
    const intensity = Math.min(1, cell.count / max);
    if (mode === "byRepo") {
      const ents = Object.entries(cell.repos).sort((a, b) => b[1] - a[1]);
      return compose(ents, REPO_COLORS, intensity, idx);
    }
    if (mode === "byDataset") {
      const ents = Object.entries(cell.datasets || {}).sort((a, b) => b[1] - a[1]);
      if (ents.length === 0) {
        // traces with no dataset attribution — desaturated
        return { background: `color-mix(in oklch, var(--fg-mute) ${15 + intensity * 30}%, var(--surface))` };
      }
      return compose(ents, DATASET_COLORS, intensity, idx);
    }
    return { background: `color-mix(in oklch, var(--c-git) ${15 + intensity * 75}%, var(--surface))` };
  };

  const monthLabels = React.useMemo(() => {
    const labels = [];
    const seen = new Set();
    const mo = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    data.forEach((w, i) => {
      const m = w[0].date.getUTCMonth();
      if (!seen.has(m)) { seen.add(m); labels.push({ col: i, label: mo[m] }); }
    });
    // Drop a label when the next month starts within 2 columns — the leading
    // partial month otherwise collides with its neighbour ("ApMay").
    return labels.filter((l, i) => !labels[i + 1] || labels[i + 1].col - l.col >= 3);
  }, [data]);

  const total = React.useMemo(() => {
    let t = 0; data.forEach(w => w.forEach(c => t += c.count)); return t;
  }, [data]);

  const [hover, setHover] = React.useState(null);
  const wrapRef = React.useRef(null);

  // Anchor the tooltip to the hovered cell (was pinned to the card corner).
  // Coordinates are converted into the wrap's own space, so the .ov-dense
  // zoom and any ancestor transforms don't throw the position off.
  const enterCell = (cell) => (e) => {
    const wrapEl = wrapRef.current;
    if (!wrapEl) return;
    const wr = wrapEl.getBoundingClientRect();
    const scale = wrapEl.offsetWidth ? wr.width / wrapEl.offsetWidth : 1;
    const r = e.currentTarget.getBoundingClientRect();
    setHover({
      cell,
      x: (r.left + r.width / 2 - wr.left) / scale,
      yTop: (r.top - wr.top) / scale,
      w: wrapEl.offsetWidth,
    });
  };

  const dsActiveNames = new Set(Object.values(DATASET_DEFS).map(d => d.name));
  const legendItems = mode === "byRepo"
    ? Object.entries(REPO_COLORS).filter(([id]) => REPO_DEFS[id])
    : mode === "byDataset"
      ? Object.entries(DATASET_COLORS).filter(([nm]) => dsActiveNames.has(nm))
      : [];

  return (
    <div className="heatmap-card">
      <div className="heatmap-head">
        <div className="hm-title">
          <span className="hm-count">{total.toLocaleString()}</span>
          <span className="hm-label">traces in the last year</span>
        </div>
        <div className="hm-toggle" role="tablist">
          <button className={"hmt " + (mode === "volume"    ? "on" : "")} onClick={() => onMode("volume")}>Volume</button>
          <button className={"hmt " + (mode === "byRepo"    ? "on" : "")} onClick={() => onMode("byRepo")}>By project</button>
          <button className={"hmt " + (mode === "byDataset" ? "on" : "")} onClick={() => onMode("byDataset")}>By dataset</button>
        </div>
      </div>
      <div className="heatmap-wrap" ref={wrapRef}>
        <div className="hm-month-row">
          {monthLabels.map((m, i) => (
            <span key={i} className="hm-month" style={{ "--month-col": m.col, left: `calc(${m.col} * (var(--hm-cell) + var(--hm-gap)))` }}>{m.label}</span>
          ))}
        </div>
        <div className="hm-grid">
          {data.map((week, wi) => (
            <div key={wi} className="hm-col">
              {week.map((cell, di) => (
                <button
                  key={di}
                  className={"hm-cell" + (selectedDay && cell.date.toDateString() === selectedDay.toDateString() ? " sel" : "")}
                  style={cellStyle(cell, wi * 7 + di)}
                  onMouseEnter={enterCell(cell)}
                  onMouseLeave={() => setHover(null)}
                  onClick={() => onSelectDay && cell.count > 0 && onSelectDay(cell)}
                  aria-label={`${cell.count} traces on ${cell.date.toDateString()}`}
                  title={cell.count > 0 && onSelectDay ? "Filter traces to this day" : undefined}
                />
              ))}
            </div>
          ))}
        </div>
        {hover && hover.cell.count > 0 && (
          <div
            className="hm-tip"
            style={{
              left: Math.min(Math.max(hover.x, 110), hover.w - 110),
              top: hover.yTop - 8,
            }}
          >
            <div className="tip-date">{hover.cell.date.toDateString()}</div>
            <div className="tip-count">{hover.cell.count} {hover.cell.count === 1 ? "trace" : "traces"}</div>
            {mode === "byDataset" && Object.keys(hover.cell.datasets || {}).length > 0 && (
              <div className="tip-repos">
                {Object.entries(hover.cell.datasets).map(([d, n]) => (
                  <div key={d} className="tip-repo">
                    <span className="tip-dot" style={{ background: DATASET_COLORS[d] }} />
                    <span className="tip-name mono">{d}</span>
                    <span className="tip-n">{n}</span>
                  </div>
                ))}
              </div>
            )}
            {mode !== "byDataset" && (
              <div className="tip-repos">
                {Object.entries(hover.cell.repos).map(([r, n]) => (
                  <div key={r} className="tip-repo">
                    <span className="tip-dot" style={{ background: REPO_COLORS[r] }} />
                    <span className="tip-name">{r}</span>
                    <span className="tip-n">{n}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      {legendItems.length > 0 && (
        <div className="hm-legend">
          {legendItems.map(([r, c]) => (
            <span key={r} className="hm-leg-item">
              <span className="hm-leg-dot" style={{ background: c }} />
              <span className={mode === "byDataset" ? "mono" : ""}>{r}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function TracesTable({ traces, groupBy, onSelect, hideRepo }) {
  const groups = React.useMemo(() => {
    const m = new Map();
    if (groupBy === "day") {
      traces.forEach(t => {
        const k = dayKey(t.timestamp);
        if (!m.has(k)) m.set(k, []);
        m.get(k).push(t);
      });
    } else if (groupBy === "repo") {
      traces.forEach(t => {
        if (!m.has(t.repo)) m.set(t.repo, []);
        m.get(t.repo).push(t);
      });
    } else { // dataset
      traces.forEach(t => {
        const k = t.dataset || "(no dataset)";
        if (!m.has(k)) m.set(k, []);
        m.get(k).push(t);
      });
    }
    return Array.from(m.entries());
  }, [traces, groupBy]);

  const showRepoCol = !hideRepo && groupBy !== "repo";

  return (
    <div className="tt-table" data-show-repo={showRepoCol ? "1" : "0"}>
      <div className="tt-head">
        <div className="tt-cell tt-c-title">Trace</div>
        <div className="tt-cell tt-c-turns">Turns</div>
        <div className="tt-cell tt-c-harness">Harness</div>
        <div className="tt-cell tt-c-model">Model</div>
        {showRepoCol && <div className="tt-cell tt-c-repo">Project</div>}
        <div className="tt-cell tt-c-ds">Dataset</div>
        <div className="tt-cell tt-c-time">When</div>
      </div>
      {groups.map(([key, items]) => (
        <div key={key} className="tt-group">
          <div className="tt-group-head">
            <span className="tt-group-label">
              {groupBy === "day" ? key
                : groupBy === "repo" ? <RepoLabel repo={key} />
                : <span className="mono">@{key}</span>}
            </span>
            <span className="tt-group-count mono">{items.length}</span>
          </div>
          {items.map(t => (
            <button key={t.id} className="tt-row" onClick={() => onSelect(t.id)}>
              <div className="tt-cell tt-c-title">
                <span className="tt-title-text">{t.title}</span>
                <HealthCounts counts={buildHealthCounts(t)} tiny />
              </div>
              <div className="tt-cell tt-c-turns mono">{t.turns}</div>
              <div className="tt-cell tt-c-harness">
                <AgentGlyph agent={t.agent} size={11} />
                <span>{t.agent.name}</span>
              </div>
              <div className="tt-cell tt-c-model mono">{t.agent.model}</div>
              {showRepoCol && (
                <div className="tt-cell tt-c-repo">
                  <RepoLabel repo={t.repo} />
                </div>
              )}
              <div className="tt-cell tt-c-ds">
                {t.dataset
                  ? <a className="ds-mention mono" onClick={(e) => e.stopPropagation()} href="#">@{t.dataset}</a>
                  : <span className="tt-empty">—</span>}
              </div>
              <div className="tt-cell tt-c-time mono">{relTime(t.timestamp)}</div>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}


function OverviewV2({ onSelectTrace, onSelectDataset, onCompare, onAllTraces, onOpenCapsules, onOpenRepo }) {
  const [mode, setMode] = React.useState("byRepo");
  const [groupBy, setGroupBy] = React.useState("day");
  // Day focus: clicking a heatmap cell filters the ledger to that day —
  // the sections in between collapse so the heatmap sits above its result.
  const [dayFilter, setDayFilter] = React.useState(null); // {date, traces}
  const selectDay = (cell) => {
    if (dayFilter && cell.date.toDateString() === dayFilter.date.toDateString()) { setDayFilter(null); return; }
    setDayFilter({ date: cell.date, traces: tracesForHeatmapDay(cell) });
  };
  // The overview carries a WIDGET of the trace ledger — the full version
  // lives at Jayfarei / Traces (onAllTraces).
  const ledger = dayFilter ? dayFilter.traces : (onAllTraces ? RECENT_TRACES.slice(0, 12) : RECENT_TRACES);
  return (
    <div className="landing landing-overview">
      <PageHero
        kicker="Workspace ledger"
        title="Overview"
        subtitle="Activity, spend, and the trace ledger it all stands on. Projects, datasets and arenas live in their own indexes."
      />

      <div className="ov-dense">
        <OvStatsSimple />

        <div className="hm-row hm-stack">
          <ContributionHeatmap data={HEATMAP} mode={mode} onMode={setMode} onSelectDay={selectDay} selectedDay={dayFilter && dayFilter.date} />
          <TokenDistribution />
        </div>

        <header className="ov-table-head">
          <div className="ov-th-left" style={{ display: "flex", alignItems: "center", minWidth: 0 }}>
            <h2 className="ov-h2">{dayFilter ? "Traces · " + dayFilter.date.toDateString() : "Recent traces"}</h2>
            {dayFilter && (
              <button className="ov-day-chip" onClick={() => setDayFilter(null)} title="Clear day filter">
                <span className="mono">{dayFilter.traces.length}</span> that day · clear ✕
              </button>
            )}
          </div>
          <div className="ov-th-actions">
            {onCompare && (
              <button className="cmp-entry" onClick={() => onCompare()} title="Compare two runs side by side">
                <span className="ce-ab">A/B</span>
                <span>Compare runs</span>
              </button>
            )}
            <div className="hm-toggle" role="tablist">
              {!dayFilter && <button className={"hmt " + (groupBy === "day" ? "on" : "")} onClick={() => setGroupBy("day")}>By day</button>}
              <button className={"hmt " + (groupBy === "repo"    ? "on" : "")} onClick={() => setGroupBy("repo")}>By project</button>
              <button className={"hmt " + (groupBy === "dataset" ? "on" : "")} onClick={() => setGroupBy("dataset")}>By dataset</button>
            </div>
          </div>
        </header>
        <TracesTable traces={ledger} groupBy={dayFilter && groupBy === "day" ? "repo" : groupBy} onSelect={onSelectTrace} />
        {onAllTraces && !dayFilter && (
          <button className="ov-viewall" onClick={() => onAllTraces()}>
            <span>View all {(window.ALL_TRACES || RECENT_TRACES).length} traces</span>
            <Icon name="chevron-right" size={13} />
          </button>
        )}
      </div>
    </div>
  );
}

// Full-screen trace ledger lives in landing-traces-index.jsx (TracesIndexPage).

window.OverviewV2 = OverviewV2;
window.ContributionHeatmap = ContributionHeatmap;
