// RepoOverview — what this project has been up to.
// Session map (who worked when), data flow (where sessions went),
// pulse stats, recent sessions, and rails for PRs / datasets / agents.

const ROV_STATUS_COLOR = {
  working:   "var(--c-user)",
  committed: "var(--c-git)",
  pushed:    "var(--c-push)",
  failed:    "var(--c-error)",
};
// Display names follow the CLI stage vocabulary.
const ROV_STATUS_LABEL = {
  working: "in flight",
  committed: "committed",
  pushed: "pushed",
  failed: "rejected",
};

const ROV_NOW = Date.UTC(2026, 3, 29, 14, 30);
const ROV_DAY = 86400000;

function rovFmtTok(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
  return String(n);
}
function rovFmtDur(s) {
  if (s >= 3600) return (s / 3600).toFixed(1) + "h";
  if (s >= 60) return Math.round(s / 60) + "m";
  return s + "s";
}

/* ── Pulse strip ─────────────────────────────────────────────── */
function RepoPulse({ sessions }) {
  const stats = React.useMemo(() => {
    const wk = sessions.filter(s => ROV_NOW - s.timestamp.getTime() < 7 * ROV_DAY);
    const prevWk = sessions.filter(s => {
      const d = ROV_NOW - s.timestamp.getTime();
      return d >= 7 * ROV_DAY && d < 14 * ROV_DAY;
    });
    const mo = sessions.filter(s => ROV_NOW - s.timestamp.getTime() < 30 * ROV_DAY);
    const tokens = mo.reduce((a, s) => a + s.tokens, 0);
    const cost = mo.reduce((a, s) => a + s.cost, 0);
    const durs = mo.map(s => s.duration_s).sort((a, b) => a - b);
    const median = durs.length ? durs[Math.floor(durs.length / 2)] : 0;
    const agents = new Set(mo.map(s => s.agent.name));
    return { week: wk.length, delta: wk.length - prevWk.length, tokens, cost, median, agents: agents.size };
  }, [sessions]);

  return (
    <div className="rov-pulse">
      <div className="rov-stat">
        <div className="k">Sessions · 7d</div>
        <div className="v mono">{stats.week}
          <span className={"rov-delta " + (stats.delta >= 0 ? "up" : "down")}>
            {stats.delta >= 0 ? "▲" : "▼"} {Math.abs(stats.delta)}
          </span>
        </div>
      </div>
      <div className="rov-stat">
        <div className="k">Tokens · 30d</div>
        <div className="v mono">{rovFmtTok(stats.tokens)}</div>
      </div>
      <div className="rov-stat">
        <div className="k">Cost · 30d</div>
        <div className="v mono">${stats.cost.toFixed(0)}</div>
      </div>
      <div className="rov-stat">
        <div className="k">Median session</div>
        <div className="v mono">{rovFmtDur(stats.median)}</div>
      </div>
      <div className="rov-stat">
        <div className="k">Agents · 30d</div>
        <div className="v mono">{stats.agents}</div>
      </div>
    </div>
  );
}

/* ── Session map — 28 days × agent lanes ─────────────────────── */
function SessionMap({ sessions, onSelectTrace }) {
  const DAYS = 28;
  const { lanes, dayLabels } = React.useMemo(() => {
    const start = ROV_NOW - (DAYS - 1) * ROV_DAY;
    const inWindow = sessions.filter(s => s.timestamp.getTime() >= start - ROV_DAY);
    // lanes keyed by agent name, ordered by session count
    const byAgent = new Map();
    inWindow.forEach(s => {
      if (!byAgent.has(s.agent.name)) byAgent.set(s.agent.name, { agent: s.agent, days: Array.from({ length: DAYS }, () => []) });
      const dayIdx = Math.floor((s.timestamp.getTime() - start) / ROV_DAY);
      if (dayIdx >= 0 && dayIdx < DAYS) byAgent.get(s.agent.name).days[dayIdx].push(s);
    });
    const lanes = Array.from(byAgent.values())
      .map(l => ({ ...l, total: l.days.reduce((a, d) => a + d.length, 0) }))
      .filter(l => l.total > 0)
      .sort((a, b) => b.total - a.total);
    const mo = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const dayLabels = Array.from({ length: DAYS }, (_, i) => {
      const d = new Date(start + i * ROV_DAY);
      return { idx: i, label: mo[d.getUTCMonth()] + " " + d.getUTCDate(), show: i % 7 === 3 };
    });
    return { lanes, dayLabels };
  }, [sessions]);

  const [hover, setHover] = React.useState(null);

  return (
    <section className="smap-card">
      <header className="rov-card-head">
        <div>
          <h3 className="rov-h3">Session map</h3>
          <span className="rov-sub">Every session in the last 4 weeks, by harness</span>
        </div>
        <div className="smap-legend">
          {Object.entries(ROV_STATUS_COLOR).map(([k, c]) => (
            <span key={k} className="smap-leg"><span className="dot" style={{ background: c }} />{ROV_STATUS_LABEL[k]}</span>
          ))}
        </div>
      </header>

      <div className="smap-body">
        {lanes.map((lane, li) => (
          <div key={lane.agent.name} className="smap-lane">
            <div className="smap-lane-label">
              <AgentGlyph agent={lane.agent} size={13} />
              <span className="nm">{lane.agent.name}</span>
              <span className="mono ct">{lane.total}</span>
            </div>
            <div className="smap-cells">
              {lane.days.map((day, di) => (
                <div
                  key={di}
                  className="smap-cell"
                  data-has={day.length > 0 ? "1" : "0"}
                  onMouseEnter={() => day.length && setHover({ li, di, day })}
                  onMouseLeave={() => setHover(null)}
                  onClick={() => day.length && onSelectTrace(day[0].id)}
                  role={day.length ? "button" : undefined}
                >
                  {day.slice(0, 3).map((s, si) => (
                    <span key={si} className="smap-dot" style={{ background: ROV_STATUS_COLOR[s.status] }} data-big={s.turns > 50 ? "1" : "0"} />
                  ))}
                  {day.length > 3 && <span className="smap-more mono">+{day.length - 3}</span>}
                </div>
              ))}
            </div>
          </div>
        ))}
        {lanes.length === 0 && <div className="rail-empty">No sessions in the last 4 weeks.</div>}

        <div className="smap-axis">
          <div className="smap-lane-label" />
          <div className="smap-cells">
            {dayLabels.map(d => (
              <div key={d.idx} className="smap-tick mono">{d.show ? d.label : ""}</div>
            ))}
          </div>
        </div>

        {hover && (
          <div
            className="smap-tip"
            style={{
              left: `calc(var(--smap-label-w) + (100% - var(--smap-label-w)) * ${(hover.di + 0.5) / 28})`,
              top: hover.li * 40 + 52,
            }}
          >
            {hover.day.slice(0, 4).map(s => (
              <div key={s.id} className="smap-tip-row">
                <span className="dot" style={{ background: ROV_STATUS_COLOR[s.status] }} />
                <span className="t">{s.title}</span>
                <span className="mono m">{s.turns} turns</span>
              </div>
            ))}
            {hover.day.length > 4 && <div className="smap-tip-more mono">+{hover.day.length - 4} more</div>}
          </div>
        )}
      </div>
    </section>
  );
}

/* ── Data flow — sessions → routes → destinations ────────────── */
function FlowDiagram({ repoId, onSelectDataset, onSelectChild }) {
  const flow = React.useMemo(() => buildRepoFlow(repoId), [repoId]);
  if (!flow) return null;

  const W = 1000, padT = 30, padB = 14, gap = 16;
  const n = flow.routes.length;
  const H = Math.max(190, 60 * n + padT + padB);
  const innerH = H - padT - padB - gap * (n - 1);
  const scale = innerH / flow.total;
  const minH = 12;

  // Compute segment heights (left node) and route/dest bar positions
  let heights = flow.routes.map(r => Math.max(minH, r.count * scale));
  const sumH = heights.reduce((a, b) => a + b, 0);
  const norm = (innerH) / sumH;
  heights = heights.map(h => h * norm);

  const leftX = 178, routeX = 470, destX = 800, barW = 7;
  let ly = padT, ry = padT;
  const segs = flow.routes.map((r, i) => {
    const h = heights[i];
    const seg = { ...r, leftY: ly, routeY: ry, h };
    ly += h;                 // left segments are contiguous
    ry += h + gap;           // route bars have gaps
    return seg;
  });
  const leftH = heights.reduce((a, b) => a + b, 0);

  const ribbon = (x1, y1, x2, y2, h1, h2) => {
    const c = (x1 + x2) / 2;
    return `M ${x1} ${y1} C ${c} ${y1}, ${c} ${y2}, ${x2} ${y2} L ${x2} ${y2 + h2} C ${c} ${y2 + h2}, ${c} ${y1 + h1}, ${x1} ${y1 + h1} Z`;
  };

  const destIcon = { dataset: "datasets", pulls: "git-branch", git: "git-commit", archive: "box" };

  return (
    <section className="flow-card">
      <header className="rov-card-head">
        <div>
          <h3 className="rov-h3">Where the data went</h3>
          <span className="rov-sub">All {flow.total} sessions stay retained in the bucket — workflows project rows, trails anchor commits</span>
        </div>
      </header>
      <div className="flow-wrap">
        <svg viewBox={`0 0 ${W} ${H}`} className="flow-svg" role="img" aria-label="Data flow from sessions to destinations">
          {/* column labels */}
          <text x={leftX} y={16} className="flow-col-label" textAnchor="end">SESSIONS</text>
          <text x={routeX + barW + 8} y={16} className="flow-col-label">ROUTE</text>
          <text x={destX + barW + 10} y={16} className="flow-col-label">DESTINATION</text>

          {/* ribbons sessions → route */}
          {segs.map(s => (
            <path key={"a" + s.id} d={ribbon(leftX + barW, s.leftY, routeX, s.routeY, s.h, s.h)} fill={s.color} opacity="0.16" />
          ))}
          {/* ribbons route → destination (same y, straight) */}
          {segs.map(s => (
            <path key={"b" + s.id} d={ribbon(routeX + barW, s.routeY, destX, s.routeY, s.h, s.h)} fill={s.color} opacity="0.16" />
          ))}

          {/* left node */}
          <rect x={leftX} y={padT} width={barW} height={leftH} rx="2.5" fill="var(--fg-mute)" opacity="0.85" />
          <text x={leftX - 12} y={padT + leftH / 2 - 4} className="flow-big mono" textAnchor="end">{flow.total}</text>
          <text x={leftX - 12} y={padT + leftH / 2 + 12} className="flow-dim" textAnchor="end">sessions captured</text>

          {/* route + dest bars and labels */}
          {segs.map(s => (
            <g key={"n" + s.id}>
              <rect x={routeX} y={s.routeY} width={barW} height={s.h} rx="2.5" fill={s.color} opacity="0.9" />
              <text x={routeX + barW + 9} y={s.routeY + s.h / 2 + 1} className="flow-route mono">{s.label}</text>
              <text x={routeX + barW + 9} y={s.routeY + s.h / 2 + 14} className="flow-dim">{s.count} sessions{s.kind === "workflow" ? " · workflow" : ""}</text>

              <rect x={destX} y={s.routeY} width={barW} height={s.h} rx="2.5" fill={s.color} opacity="0.9" />
              <text x={destX + barW + 10} y={s.routeY + s.h / 2 + 1} className="flow-dest">{s.dest.label}</text>
              <text x={destX + barW + 10} y={s.routeY + s.h / 2 + 14} className="flow-dim">{s.dest.sub}</text>
            </g>
          ))}
        </svg>

        <div className="flow-actions">
          {flow.routes.filter(r => r.dest.kind === "dataset" && r.dest.dsId).map(r => (
            <button key={r.id} className="flow-chip" onClick={() => onSelectDataset(r.dest.dsId)}>
              <Icon name="datasets" size={12} />
              <span className="mono">{r.dest.label}</span>
              <Icon name="chevron-right" size={11} />
            </button>
          ))}
          <button className="flow-chip" onClick={() => onSelectChild("pulls")}>
            <Icon name="git-branch" size={12} />
            <span>Pull request trails</span>
            <Icon name="chevron-right" size={11} />
          </button>
        </div>
      </div>
    </section>
  );
}

/* ── Rails ───────────────────────────────────────────────────── */
function RailPulls({ repoId, onSelectChild }) {
  const prs = (typeof REPO_PULLS !== "undefined" && REPO_PULLS[repoId]) || [];
  const open = prs.filter(p => p.status === "open");
  return (
    <div className="rail-card">
      <div className="rail-h">Pull requests</div>
      {open.length === 0 && <div className="rail-empty">No open pull requests</div>}
      {open.map(pr => (
        <button key={pr.id} className="rov-pr-row" onClick={() => onSelectChild("pulls")}>
          <span className="rov-pr-dot" data-v={pr.verdict} />
          <span className="mono num">#{pr.number}</span>
          <span className="ttl">{pr.title}</span>
        </button>
      ))}
      {prs.length > 0 && (
        <button className="rail-foot-link" onClick={() => onSelectChild("pulls")}>
          <span>All pull requests</span>
          <Icon name="chevron-right" size={11} />
        </button>
      )}
    </div>
  );
}

function RailDatasets({ def, onSelectDataset }) {
  return (
    <div className="rail-card">
      <div className="rail-h">Datasets fed</div>
      {(!def.datasets || def.datasets.length === 0) && <div className="rail-empty">No datasets yet</div>}
      {def.datasets && def.datasets.map(dsId => {
        const ds = DATASET_DEFS[dsId];
        if (!ds) return null;
        return (
          <button key={dsId} className="rov-ds-row" onClick={() => onSelectDataset(dsId)}>
            <Icon name="datasets" size={12} />
            <span className="mono nm">@{ds.name}</span>
            <span className="mono rows">{ds.rows.toLocaleString()} rows</span>
            <span className={"remote-tag remote-" + ds.remote}>{ds.remote}</span>
          </button>
        );
      })}
    </div>
  );
}

function RailAgents({ sessions }) {
  const rows = React.useMemo(() => {
    const m = new Map();
    sessions.forEach(s => {
      if (!m.has(s.agent.name)) m.set(s.agent.name, { agent: s.agent, count: 0 });
      m.get(s.agent.name).count++;
    });
    return Array.from(m.values()).sort((a, b) => b.count - a.count);
  }, [sessions]);
  const max = rows[0]?.count || 1;
  return (
    <div className="rail-card">
      <div className="rail-h">Harnesses</div>
      {rows.map(r => (
        <div key={r.agent.name} className="rov-agent-row">
          <AgentGlyph agent={r.agent} size={12} />
          <span className="nm">{r.agent.name}</span>
          <span className="bar"><span className="fill" style={{ width: (r.count / max) * 100 + "%" }} /></span>
          <span className="mono ct">{r.count}</span>
        </div>
      ))}
    </div>
  );
}

/* ── Live session strip — only what capture actually knows ───── */
function RovLiveStrip({ def, onSelectTrace }) {
  const wt = def.working_tree;
  if (!wt.current_trace) return null;
  return (
    <button className="rov-live" onClick={() => onSelectTrace(wt.current_trace.id)}>
      <span className="wt-pulse" />
      <span className="rov-live-lbl">In flight</span>
      <span className="rov-live-ttl">{wt.current_trace.title}</span>
      <span className="mono rov-live-id">{wt.current_trace.id}</span>
      <span className="mono rov-live-br">on {def.default_branch}</span>
      <Icon name="chevron-right" size={13} className="rov-live-arrow" />
    </button>
  );
}

/* ── Page ────────────────────────────────────────────────────── */
function RepoOverview({ repoId, onSelectTrace, onSelectChild, onSelectDataset }) {
  const def = REPO_DEFS[repoId];
  if (!def) return null;
  const sessions = React.useMemo(() => buildRepoSessions(repoId), [repoId]);
  const recent = sessions.slice(0, 7);

  return (
    <div className="landing landing-repo">
      <header className="repo-hero">
        <div className="rh-icon"><Icon name="git-branch" size={18} /></div>
        <div className="rh-title">
          <h1 className="rh-name"><span className="ns">{def.ns}/</span><span className="nm">{def.nm}</span></h1>
          <p className="rh-desc">{def.description}</p>
        </div>
        <div className="rh-meta">
          <div className="rh-stat"><div className="k">Branch</div><div className="v mono">{def.default_branch}</div></div>
          <div className="rh-stat"><div className="k">In flight</div><div className="v mono">{def.open_traces}</div></div>
          <div className="rh-stat"><div className="k">Sessions</div><div className="v mono">{def.total_traces}</div></div>
          <div className="rh-stat"><div className="k">Last push</div><div className="v">{def.last_push}</div></div>
        </div>
      </header>

      <RepoPulse sessions={sessions} />

      <RovLiveStrip def={def} onSelectTrace={onSelectTrace} />

      <SessionMap sessions={sessions} onSelectTrace={onSelectTrace} />

      <FlowDiagram repoId={repoId} onSelectDataset={onSelectDataset} onSelectChild={onSelectChild} />

      <div className="repo-grid">
        <section className="repo-col">
          <header className="rov-card-head plain">
            <h3 className="rov-h3">Recent sessions</h3>
            <button className="rail-foot-link inline" onClick={() => onSelectChild("traces")}>
              <span>All {def.total_traces} sessions</span>
              <Icon name="chevron-right" size={11} />
            </button>
          </header>
          <TracesTable traces={recent} groupBy="day" onSelect={onSelectTrace} hideRepo />
        </section>
        <aside className="repo-side">
          <RailPulls repoId={repoId} onSelectChild={onSelectChild} />
          <RailDatasets def={def} onSelectDataset={onSelectDataset} />
          <RailAgents sessions={sessions} />
        </aside>
      </div>
    </div>
  );
}

window.RepoOverview = RepoOverview;
