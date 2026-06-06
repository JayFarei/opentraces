// Evals page — SOPs (rules) for coding agents.
// Same shape as Intents but oriented around *violations*, with a stop-sign tint.

function EvalsPage({ onSelectTrace }) {
  const [activeId, setActiveId] = React.useState(SOPS[0].id);
  const [filter, setFilter] = React.useState("");

  const stream  = React.useMemo(() => buildStreamSeries(SOPS.map(s => ({ ...s, count: s.violations })), 8211, 60), []);
  const matched = React.useMemo(() => buildMatched(SOPS, RECENT_TRACES, 33), []);
  const active = SOPS.find(s => s.id === activeId) || SOPS[0];

  const filtered = SOPS.filter(s => !filter || s.name.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="landing landing-page">
      <PageHero
        kicker="Conversation intelligence"
        title="Evals"
        subtitle="Standard operating procedures the agent should follow. Violations are graded by an LLM judge against the rule statement and surfaced here."
        scope="last 1h · 10 SOPs · 2,883 violations"
        actions={
          <div className="ph-action-group">
            <ToolBtn icon="plus" label="Add SOP" />
            <ToolBtn icon="tool" label="Auto-generate" />
            <ToolBtn icon="git-commit" label="Run" primary />
          </div>
        }
      />

      <div className="intel-grid">
        <aside className="intel-list-card">
          <header className="ilc-head">
            <div className="ilc-h-l">
              <h2 className="ilc-h">SOPs</h2>
              <span className="ilc-count mono">{SOPS.length}</span>
            </div>
          </header>
          <div className="ilc-filter">
            <Icon name="search" size={12} />
            <input
              type="text"
              placeholder="Filter SOPs…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              spellCheck={false}
            />
          </div>
          <div className="ilc-list">
            {filtered.map(it => (
              <button
                key={it.id}
                className="ilc-row"
                aria-current={activeId === it.id}
                onClick={() => setActiveId(it.id)}
              >
                <span className="ilc-dot" style={{ background: it.color }} />
                <div className="ilc-row-body">
                  <div className="ilc-row-top">
                    <span className="ilc-name">{it.name}</span>
                    <span className="ilc-num mono">{it.violations}</span>
                  </div>
                  <div className="ilc-desc">{it.description}</div>
                </div>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="ilc-empty">No SOPs match "{filter}".</div>
            )}
          </div>
        </aside>

        <section className="intel-detail">
          <div className="intel-chart-card">
            <header className="icc-head">
              <div>
                <div className="icc-kicker">SOP violations</div>
                <div className="icc-sub">All SOPs, stacked · last 60 minutes</div>
              </div>
            </header>
            <StreamChart data={stream} height={220} topN={4} />
          </div>

          <div className="intel-matched-card">
            <header className="icc-head">
              <div>
                <div className="icc-kicker">Violating traces</div>
                <div className="icc-sub">
                  <span className="ilc-dot inline" style={{ background: active.color }} />
                  <span className="mono">@{active.name}</span>
                  <span className="dot-sep" />
                  <span>{matched[active.id]?.length || 0} traces</span>
                </div>
              </div>
              <ToolBtn icon="tool" label="Summarize" />
            </header>
            <div className="match-list">
              {(matched[active.id] || []).map((row, i) => (
                <MatchedRow
                  key={i}
                  row={row}
                  label="1 violation"
                  onOpen={() => onSelectTrace && onSelectTrace(row.traceId)}
                />
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

window.EvalsPage = EvalsPage;
