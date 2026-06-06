// Intents page — patterns observed across traces.
// Layout: hero → 2-col (intent list + selected detail with stream chart and matched traces).

function IntentsPage({ onSelectTrace }) {
  const [activeId, setActiveId] = React.useState(INTENTS[0].id);
  const [filter, setFilter] = React.useState("");

  const stream = React.useMemo(() => buildStreamSeries(INTENTS, 7117, 60), []);
  const matched = React.useMemo(() => buildMatched(INTENTS, RECENT_TRACES, 91), []);
  const active = INTENTS.find(i => i.id === activeId) || INTENTS[0];

  const filtered = INTENTS.filter(i => !filter || i.name.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="landing landing-page">
      <PageHero
        kicker="Conversation intelligence"
        title="Intents"
        subtitle="Patterns the agents fall into across your traces. Edit the description, regrade the lookback window, and surface every matching trace."
        scope="last 1h · 6 projects · 482 traces"
        actions={
          <div className="ph-action-group">
            <ToolBtn icon="plus" label="Add intent" />
            <ToolBtn icon="tool" label="Auto-generate" />
            <ToolBtn icon="git-commit" label="Run" primary />
          </div>
        }
      />

      <div className="intel-grid">
        {/* Left column: intent catalogue */}
        <aside className="intel-list-card">
          <header className="ilc-head">
            <div className="ilc-h-l">
              <h2 className="ilc-h">Intents</h2>
              <span className="ilc-count mono">{INTENTS.length}</span>
            </div>
          </header>
          <div className="ilc-filter">
            <Icon name="search" size={12} />
            <input
              type="text"
              placeholder="Filter intents…"
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
                    <span className="ilc-num mono">{it.count}</span>
                  </div>
                  <div className="ilc-desc">{it.description}</div>
                </div>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="ilc-empty">No intents match "{filter}".</div>
            )}
          </div>
        </aside>

        {/* Right column: detail */}
        <section className="intel-detail">
          <div className="intel-chart-card">
            <header className="icc-head">
              <div>
                <div className="icc-kicker">Intent activity</div>
                <div className="icc-sub">All intents, stacked · last 60 minutes</div>
              </div>
            </header>
            <StreamChart data={stream} height={220} topN={4} />
          </div>

          <div className="intel-matched-card">
            <header className="icc-head">
              <div>
                <div className="icc-kicker">Matched traces</div>
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
                  label="1 intent match"
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

window.IntentsPage = IntentsPage;
