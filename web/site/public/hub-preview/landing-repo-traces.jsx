// RepoTracesPage — every session captured in this project,
// newest first, with status / harness / search filtering.
// Same table vocabulary as the workspace ledger, scoped to one project.

// Stage names follow the CLI: in-flight capture, then committed → pushed,
// with rejected sessions kept for the record.
const RTR_STATUS_TABS = [
  { id: "all",       label: "All" },
  { id: "working",   label: "In flight", color: "var(--c-user)" },
  { id: "committed", label: "Committed", color: "var(--c-git)" },
  { id: "pushed",    label: "Pushed",    color: "var(--c-push)" },
  { id: "failed",    label: "Rejected",  color: "var(--c-error)" },
];

function RtrDensityStrip({ sessions }) {
  // 8-week daily session count strip — quick recency texture above the table
  const DAYS = 56;
  const now = Date.UTC(2026, 3, 29, 14, 30);
  const counts = React.useMemo(() => {
    const arr = Array.from({ length: DAYS }, () => 0);
    sessions.forEach(s => {
      const idx = DAYS - 1 - Math.floor((now - s.timestamp.getTime()) / 86400000);
      if (idx >= 0 && idx < DAYS) arr[idx]++;
    });
    return arr;
  }, [sessions]);
  const max = Math.max(...counts, 1);
  return (
    <div className="rtr-density" title="Sessions per day · last 8 weeks">
      {counts.map((c, i) => (
        <span key={i} className="rtr-bar" style={{ height: Math.max(2, (c / max) * 26) + "px", opacity: c === 0 ? 0.25 : 0.45 + (c / max) * 0.55 }} />
      ))}
    </div>
  );
}

function RepoTracesPage({ repoId, onSelectTrace }) {
  const def = REPO_DEFS[repoId];
  if (!def) return null;
  const sessions = React.useMemo(() => buildRepoSessions(repoId), [repoId]);

  const [status, setStatus] = React.useState("all");
  const [agent, setAgent] = React.useState(null);     // agent name or null
  const [query, setQuery] = React.useState("");
  const [groupBy, setGroupBy] = React.useState("day");

  const agents = React.useMemo(() => {
    const m = new Map();
    sessions.forEach(s => {
      if (!m.has(s.agent.name)) m.set(s.agent.name, { agent: s.agent, count: 0 });
      m.get(s.agent.name).count++;
    });
    return Array.from(m.values()).sort((a, b) => b.count - a.count);
  }, [sessions]);

  const statusCounts = React.useMemo(() => {
    const c = { all: sessions.length };
    sessions.forEach(s => { c[s.status] = (c[s.status] || 0) + 1; });
    return c;
  }, [sessions]);

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    return sessions.filter(s =>
      (status === "all" || s.status === status) &&
      (!agent || s.agent.name === agent) &&
      (!q || s.title.toLowerCase().includes(q) || s.id.includes(q))
    );
  }, [sessions, status, agent, query]);

  return (
    <div className="landing landing-repo">
      <header className="repo-hero">
        <div className="rh-icon"><Icon name="activity" size={18} /></div>
        <div className="rh-title">
          <h1 className="rh-name"><span className="ns">{def.ns}/{def.nm} ·</span><span className="nm"> Traces</span></h1>
          <p className="rh-desc">Every session captured in this project, newest first.</p>
        </div>
        <div className="rh-meta">
          <div className="rh-stat"><div className="k">Sessions</div><div className="v mono">{sessions.length}</div></div>
          <div className="rh-stat"><div className="k">In flight</div><div className="v mono">{statusCounts.working || 0}</div></div>
          <div className="rh-stat"><div className="k">Rejected</div><div className="v mono">{statusCounts.failed || 0}</div></div>
        </div>
      </header>

      <div className="rtr-toolbar">
        <div className="rtr-tabs" role="tablist">
          {RTR_STATUS_TABS.map(t => (
            <button
              key={t.id}
              className="rtr-tab"
              aria-current={status === t.id}
              onClick={() => setStatus(t.id)}
            >
              {t.color && <span className="dot" style={{ background: t.color }} />}
              <span>{t.label}</span>
              <span className="mono c">{statusCounts[t.id] || 0}</span>
            </button>
          ))}
        </div>

        <div className="rtr-right">
          <label className="rtr-search">
            <Icon name="search" size={13} />
            <input
              type="text"
              placeholder="Filter sessions…"
              value={query}
              onChange={e => setQuery(e.target.value)}
            />
          </label>
          <div className="hm-toggle" role="tablist">
            <button className={"hmt " + (groupBy === "day" ? "on" : "")} onClick={() => setGroupBy("day")}>By day</button>
            <button className={"hmt " + (groupBy === "dataset" ? "on" : "")} onClick={() => setGroupBy("dataset")}>By dataset</button>
          </div>
        </div>
      </div>

      <div className="rtr-agents">
        <span className="rtr-agents-lbl">Harness</span>
        <button className="rtr-agent-chip" aria-current={agent === null} onClick={() => setAgent(null)}>
          <span>All</span>
        </button>
        {agents.map(a => (
          <button
            key={a.agent.name}
            className="rtr-agent-chip"
            aria-current={agent === a.agent.name}
            onClick={() => setAgent(agent === a.agent.name ? null : a.agent.name)}
          >
            <AgentGlyph agent={a.agent} size={11} />
            <span>{a.agent.name}</span>
            <span className="mono c">{a.count}</span>
          </button>
        ))}
        <div className="grow" />
        <RtrDensityStrip sessions={sessions} />
      </div>

      {filtered.length > 0 ? (
        <TracesTable traces={filtered} groupBy={groupBy} onSelect={onSelectTrace} hideRepo />
      ) : (
        <div className="rtr-empty">
          <Icon name="search" size={18} />
          <div className="h">No sessions match</div>
          <div className="s">Try clearing the status, harness or text filters.</div>
        </div>
      )}

      <div className="rtr-foot mono">
        Showing {filtered.length} of {sessions.length} sessions
      </div>
    </div>
  );
}

window.RepoTracesPage = RepoTracesPage;
