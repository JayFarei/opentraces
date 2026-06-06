// Overview — ledger of traces tied to projects + datasets.
//
// Layout:
//   1. Hero stat strip (total traces, active projects, active datasets, this week)
//   2. Contribution heatmap (toggle: Volume / By project / By dataset)
//   3. Dataset summary tiles — one card per dataset, sorted by recent activity
//   4. Ledger table of traces (group by day / project / dataset)

const DATASET_COLORS = {
  "claude-eval-v3":   "var(--c-read)",
  "keystrokes-2026":  "var(--c-plan)",
  "edge-traces":      "var(--c-write)",
  "rag-fixtures":     "var(--c-exec)",
  "shortcuts-bench":  "var(--c-user)",
};

const REPO_COLORS = {
  "jayfarei/opentraces":     "var(--c-write)",
  "jayfarei/datafetch":      "var(--c-read)",
  "jayfarei/lazymem":        "var(--c-plan)",
  "jayfarei/clarify":        "var(--c-exec)",
  "jayfarei/open-data":      "var(--c-git)",
  "jayfarei/koreader-kiosk": "var(--c-user)",
};

function ProjectTokenDistribution() {
  // Synthesize year-to-date token spend per project. Seeded so it's stable.
  const rows = React.useMemo(() => {
    let seed = 414141;
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    const ids = Object.keys(REPO_DEFS);
    const out = ids.map(id => {
      const d = REPO_DEFS[id];
      const base = d.total_traces * (180_000 + rnd() * 120_000);
      const tokens = Math.round(base * (0.6 + rnd() * 0.9));
      const inTok = Math.round(tokens * (0.7 + rnd() * 0.15));
      const outTok = tokens - inTok;
      const cost = (inTok / 1_000_000) * 3 + (outTok / 1_000_000) * 15;
      const delta = (rnd() - 0.45) * 0.6;
      return { id, ns: d.ns, nm: d.nm, tokens, inTok, outTok, cost, delta, traces: d.total_traces };
    });
    out.sort((a, b) => b.tokens - a.tokens);
    return out;
  }, []);

  const total = rows.reduce((a, r) => a + r.tokens, 0);
  const totalCost = rows.reduce((a, r) => a + r.cost, 0);

  const fmtTok = (n) => {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
    return String(n);
  };
  const fmtCost = (c) => "$" + (c >= 1000 ? c.toFixed(0) : c.toFixed(2));
  const fmtDelta = (d) => (d > 0 ? "+" : "") + (d * 100).toFixed(0) + "%";

  return (
    <section className="tok-dist">
      <header className="tok-dist-head">
        <div>
          <div className="tok-dist-kicker">Tokens · Last 365d</div>
          <div className="tok-dist-title">Distribution by project</div>
        </div>
        <div className="tok-dist-totals">
          <div className="td-stat">
            <div className="td-stat-v mono">{fmtTok(total)}</div>
            <div className="td-stat-k">tokens spent</div>
          </div>
          <div className="td-stat">
            <div className="td-stat-v mono">{fmtCost(totalCost)}</div>
            <div className="td-stat-k">total cost</div>
          </div>
          <div className="td-stat">
            <div className="td-stat-v mono">{rows.length}</div>
            <div className="td-stat-k">projects</div>
          </div>
        </div>
      </header>

      {/* Stacked horizontal bar */}
      <div className="tok-dist-bar" role="img" aria-label="Token distribution by project">
        {rows.map((r) => {
          const pct = (r.tokens / total) * 100;
          const color = REPO_COLORS[r.id] || "var(--c-git)";
          return (
            <div
              key={r.id}
              className="tdb-seg"
              style={{ flexBasis: pct + "%", background: color }}
              title={`${r.ns}/${r.nm} · ${fmtTok(r.tokens)} (${pct.toFixed(1)}%)`}
            >
              {pct > 6 && <span className="tdb-seg-label mono">{r.nm}</span>}
            </div>
          );
        })}
      </div>

      {/* Per-project chips */}
      <div className="tok-dist-chips">
        {rows.map((r) => {
          const pct = (r.tokens / total) * 100;
          const color = REPO_COLORS[r.id] || "var(--c-git)";
          const up = r.delta >= 0;
          return (
            <div key={r.id} className="td-chip">
              <span className="td-chip-swatch" style={{ background: color }} />
              <div className="td-chip-main">
                <div className="td-chip-name">
                  <span className="ns">{r.ns}/</span><span className="nm">{r.nm}</span>
                </div>
                <div className="td-chip-meta">
                  <span className="mono">{fmtTok(r.tokens)}</span>
                  <span className="td-chip-sep">·</span>
                  <span className="mono">{pct.toFixed(1)}%</span>
                  <span className="td-chip-sep">·</span>
                  <span className="mono">{fmtCost(r.cost)}</span>
                </div>
              </div>
              <div className={"td-chip-delta " + (up ? "up" : "down")} title="vs prior 365d window">
                <span className="arrow">{up ? "▲" : "▼"}</span>
                <span className="mono">{fmtDelta(Math.abs(r.delta))}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ProjectTokenLeaderboard() {
  // Synthesize year-to-date token spend per project. Seeded so it's stable.
  const rows = React.useMemo(() => {
    let seed = 414141;
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    const ids = Object.keys(REPO_DEFS);
    const out = ids.map(id => {
      const d = REPO_DEFS[id];
      // base: scale by total_traces with variance
      const base = d.total_traces * (180_000 + rnd() * 120_000);
      const tokens = Math.round(base * (0.6 + rnd() * 0.9));
      const traces = d.total_traces;
      // crude split: 75% input, 25% output
      const inTok = Math.round(tokens * (0.7 + rnd() * 0.15));
      const outTok = tokens - inTok;
      const cost = (inTok / 1_000_000) * 3 + (outTok / 1_000_000) * 15; // $3/$15 per Mtok
      // week-over-week delta, signed
      const delta = (rnd() - 0.45) * 0.6;
      return { id, ns: d.ns, nm: d.nm, tokens, inTok, outTok, cost, traces, delta };
    });
    out.sort((a, b) => b.tokens - a.tokens);
    return out;
  }, []);

  const total = rows.reduce((a, r) => a + r.tokens, 0);
  const max = rows[0]?.tokens || 1;

  const fmtTok = (n) => {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
    return String(n);
  };
  const fmtCost = (c) => "$" + (c >= 1000 ? c.toFixed(0) : c.toFixed(2));

  return (
    <aside className="tok-card">
      <header className="tok-head">
        <div className="tok-kicker">Tokens · Last 365d</div>
        <div className="tok-total">
          <span className="tok-total-v mono">{fmtTok(total)}</span>
          <span className="tok-total-s">total spend {fmtCost(rows.reduce((a, r) => a + r.cost, 0))}</span>
        </div>
      </header>
      <div className="tok-list">
        {rows.map((r, i) => {
          const pct = (r.tokens / total) * 100;
          const color = REPO_COLORS[r.id] || "var(--c-git)";
          return (
            <div key={r.id} className="tok-row" title={`${r.ns}/${r.nm} · ${r.tokens.toLocaleString()} tokens · in ${r.inTok.toLocaleString()} · out ${r.outTok.toLocaleString()} · cost ${fmtCost(r.cost)}`}>
              <span className="tok-rank mono">{i + 1}</span>
              <span className="tok-name-w">
                <span className="tok-name">
                  <span className="ns">{r.ns}/</span><span className="nm">{r.nm}</span>
                </span>
                <span className="tok-underline" style={{ background: color, width: Math.max(18, pct * 1.2) + "%" }} />
              </span>
              <span className="tok-amt mono">{fmtTok(r.tokens)}</span>
              <span className="tok-pct mono">{pct.toFixed(0)}%</span>
            </div>
          );
        })}
      </div>
      <a className="tok-foot" href="#" onClick={(e) => e.preventDefault()}>
        <span>View all</span>
        <Icon name="chevron-right" size={12} />
      </a>
    </aside>
  );
}

function ContributionHeatmap({ data, mode, onMode }) {
  const max = React.useMemo(() => {
    let m = 0;
    data.forEach(w => w.forEach(c => { if (c.count > m) m = c.count; }));
    return m;
  }, [data]);

  const cellStyle = (cell) => {
    if (cell.count === 0) return { background: "color-mix(in oklch, var(--fg) 6%, transparent)" };
    const intensity = Math.min(1, cell.count / max);
    if (mode === "byRepo") {
      const dominant = Object.entries(cell.repos).sort((a,b) => b[1]-a[1])[0]?.[0];
      const color = REPO_COLORS[dominant] || "var(--c-git)";
      return { background: `color-mix(in oklch, ${color} ${20 + intensity * 70}%, var(--surface))` };
    }
    if (mode === "byDataset") {
      const ents = Object.entries(cell.datasets || {});
      if (ents.length === 0) {
        // traces with no dataset attribution — desaturated
        return { background: `color-mix(in oklch, var(--fg-mute) ${15 + intensity * 30}%, var(--surface))` };
      }
      const dominant = ents.sort((a,b) => b[1]-a[1])[0]?.[0];
      const color = DATASET_COLORS[dominant] || "var(--c-git)";
      return { background: `color-mix(in oklch, ${color} ${20 + intensity * 70}%, var(--surface))` };
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
    return labels;
  }, [data]);

  const total = React.useMemo(() => {
    let t = 0; data.forEach(w => w.forEach(c => t += c.count)); return t;
  }, [data]);

  const [hover, setHover] = React.useState(null);

  const legendItems = mode === "byRepo"
    ? Object.entries(REPO_COLORS)
    : mode === "byDataset"
      ? Object.entries(DATASET_COLORS)
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
      <div className="heatmap-wrap">
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
                  className="hm-cell"
                  style={cellStyle(cell)}
                  onMouseEnter={() => setHover({ cell, wi, di })}
                  onMouseLeave={() => setHover(null)}
                  aria-label={`${cell.count} traces on ${cell.date.toDateString()}`}
                />
              ))}
            </div>
          ))}
        </div>
        {hover && hover.cell.count > 0 && (
          <div className="hm-tip">
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

function OverviewStats({ traces }) {
  const stats = React.useMemo(() => {
    // Year-to-date token totals derived the same way as the leaderboard,
    // so the panel and the strip agree.
    let seed = 414141;
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    let totalIn = 0, totalOut = 0;
    Object.values(REPO_DEFS).forEach(d => {
      const base = d.total_traces * (180_000 + rnd() * 120_000);
      const tokens = Math.round(base * (0.6 + rnd() * 0.9));
      const inTok = Math.round(tokens * (0.7 + rnd() * 0.15));
      totalIn += inTok;
      totalOut += tokens - inTok;
    });
    // Total traces YTD: roughly 476 from the heatmap; sum the cells
    let totalTraces = 0;
    HEATMAP.forEach(week => week.forEach(c => { totalTraces += c.count; }));
    return {
      traces: totalTraces,
      tokensIn: totalIn,
      tokensOut: totalOut,
      projects: Object.keys(REPO_DEFS).length,
      datasets: Object.keys(DATASET_DEFS).length,
    };
  }, []);

  const fmt = (n) => {
    if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1).replace(/\.0$/, "") + "B";
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  };

  return (
    <div className="ov-stats">
      <div className="ov-stat">
        <span className="ov-stat-ico s-traces"><Icon name="activity" size={20} /></span>
        <div className="ov-stat-body">
          <div className="k">Traces</div>
          <div className="v mono">{fmt(stats.traces)}</div>
          <div className="s">Last 12 months</div>
        </div>
      </div>
      <div className="ov-stat">
        <span className="ov-stat-ico s-in"><Icon name="down-line" size={18} /></span>
        <div className="ov-stat-body">
          <div className="k">Tokens In</div>
          <div className="v mono">{fmt(stats.tokensIn)}</div>
          <div className="s">Last 12 months</div>
        </div>
      </div>
      <div className="ov-stat">
        <span className="ov-stat-ico s-out"><Icon name="up-line" size={18} /></span>
        <div className="ov-stat-body">
          <div className="k">Tokens Out</div>
          <div className="v mono">{fmt(stats.tokensOut)}</div>
          <div className="s">Last 12 months</div>
        </div>
      </div>
      <div className="ov-stat">
        <span className="ov-stat-ico s-repos"><Icon name="repo" size={18} /></span>
        <div className="ov-stat-body">
          <div className="k">Projects</div>
          <div className="v mono">{stats.projects}</div>
          <div className="s">Total</div>
        </div>
      </div>
      <div className="ov-stat">
        <span className="ov-stat-ico s-datasets"><Icon name="datasets" size={18} /></span>
        <div className="ov-stat-body">
          <div className="k">Datasets</div>
          <div className="v mono">{stats.datasets}</div>
          <div className="s">Total</div>
        </div>
      </div>
    </div>
  );
}

// Compact formatters for the icon-led dataset tiles
function fmtRows(n) {
  if (n >= 1000) {
    const k = n / 1000;
    return (k >= 10 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, "")) + "k";
  }
  return n.toLocaleString();
}
function shortTime(t) {
  if (!t) return t;
  if (/yesterday/i.test(t)) return "1d";
  let m = t.match(/(\d+)\s*d/i);          if (m) return m[1] + "d";
  m = t.match(/(\d+)\s*h/i);              if (m) return m[1] + "h";
  m = t.match(/(\d+)\s*m(?!o)/i);         if (m) return m[1] + "m";
  m = t.match(/(\d+)\s*w/i);              if (m) return m[1] + "w";
  if (/just now|now/i.test(t)) return "now";
  return t;
}

function DatasetTiles({ onSelectDataset }) {
  // Sort by inbox_count desc, then by name
  const tiles = React.useMemo(() => {
    return Object.entries(DATASET_DEFS)
      .map(([id, d]) => ({ id, ...d }))
      .sort((a, b) => (b.inbox_count - a.inbox_count) || a.name.localeCompare(b.name));
  }, []);

  return (
    <section className="ov-tiles">
      <header className="ov-tiles-head">
        <h2 className="ov-h2">Datasets</h2>
        <span className="ov-tiles-sub">{tiles.length} active · sorted by inbox</span>
      </header>
      <div className="ov-tile-grid">
        {tiles.map(d => {
          const hasRemote = !!d.remote_url;
          const diff = d.local_commits - d.remote_commits;
          return (
            <button key={d.id} className="ov-tile" onClick={() => onSelectDataset(d.id)}>
              <div className="ot-row1">
                <span className="ot-name mono">{d.name}</span>
                {hasRemote && (
                  <a
                    className="ot-hf-link"
                    href={d.remote_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    title={`Open ${d.remote_repo} on Hugging Face`}
                  >
                    <HuggingFaceLogo size={13} />
                    <Icon name="external" size={10} className="ot-hf-ext" />
                  </a>
                )}
                {!hasRemote && (
                  <span className="ot-hf-link local-only" title="Local only — no remote">
                    <span className="ot-hf-localdot" />
                  </span>
                )}
              </div>

              <div className="ot-metrics">
                <span className="ot-metric" title={`${d.rows.toLocaleString()} rows`}>
                  <Icon name="datasets" size={13} className="ot-mi" />
                  <span className="ot-mv mono">{fmtRows(d.rows)}</span>
                </span>
                <span className="ot-metric" title={`Last update · ${d.last_update}`}>
                  <Icon name="clock" size={13} className="ot-mi" />
                  <span className="ot-mv">{shortTime(d.last_update)}</span>
                </span>
                {!hasRemote && (
                  <span className="ot-metric ot-sync muted" title="Local only — no remote">
                    <Icon name="repo" size={13} className="ot-mi" />
                    <span className="ot-mv">local</span>
                  </span>
                )}
                {hasRemote && diff === 0 && (
                  <span className="ot-metric ot-sync ok" title="In sync with remote">
                    <Icon name="git-commit" size={13} className="ot-mi" />
                    <span className="ot-mv">synced</span>
                  </span>
                )}
                {hasRemote && diff > 0 && (
                  <span className="ot-metric ot-sync ahead" title={`${diff} commits ahead — waiting to push`}>
                    <Icon name="up-line" size={13} className="ot-mi" />
                    <span className="ot-mv mono">{diff}</span>
                  </span>
                )}
                {hasRemote && diff < 0 && (
                  <span className="ot-metric ot-sync behind" title={`${-diff} commits behind — pull to update`}>
                    <Icon name="down-line" size={13} className="ot-mi" />
                    <span className="ot-mv mono">{-diff}</span>
                  </span>
                )}
                {d.inbox_count > 0 && (
                  <span className="ot-metric ot-inbox-m" title={`${d.inbox_count} in inbox`}>
                    <Icon name="inbox" size={13} className="ot-mi" />
                    <span className="ot-mv mono">{d.inbox_count}</span>
                  </span>
                )}
                {d.auto_policy && (
                  <span className="ot-metric ot-auto-m" title="Auto-sync policy">
                    <span className="policy-tag auto">auto</span>
                  </span>
                )}
              </div>
            </button>

          );
        })}
        <button className="ov-tile ov-tile-more" onClick={() => onSelectDataset && onSelectDataset("__view_all__")}>
          <div className="otm-glyph">
            <Icon name="datasets" size={15} />
          </div>
          <div className="otm-text">
            <div className="otm-label">View all datasets</div>
            <div className="otm-sub mono">{tiles.length} total · browse &amp; create</div>
          </div>
          <Icon name="chevron-right" size={14} className="otm-arrow" />
        </button>
      </div>
    </section>
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

function TracesLanding({ onSelectTrace, onSelectDataset, onCompare }) {
  const [mode, setMode] = React.useState("byRepo");
  const [groupBy, setGroupBy] = React.useState("day");
  return (
    <div className="landing landing-overview">
      <PageHero
        kicker="Workspace ledger"
        title="Overview"
        subtitle="Ledger of every trace, with ties to your projects and datasets."
      />

      <div className="ov-dense">
        <OverviewStats traces={RECENT_TRACES} />

        <div className="hm-row hm-stack">
          <ContributionHeatmap data={HEATMAP} mode={mode} onMode={setMode} />
          <ProjectTokenDistribution />
        </div>

        <DatasetTiles onSelectDataset={onSelectDataset} />

        <header className="ov-table-head">
          <h2 className="ov-h2">All traces</h2>
          <div className="ov-th-actions">
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
        </header>
        <TracesTable traces={RECENT_TRACES} groupBy={groupBy} onSelect={onSelectTrace} />
      </div>
    </div>
  );
}

window.TracesLanding = TracesLanding;
window.TracesTable = TracesTable;
