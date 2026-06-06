// Spotlight page — natural language search across all traces.

function SpotlightPage({ initialQuery, onConsumeQuery }) {
  const [q, setQ] = React.useState("");
  const [running, setRunning] = React.useState(false);
  const [results, setResults] = React.useState(null);
  const inputRef = React.useRef(null);

  React.useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const run = (text) => {
    const v = (text ?? q).trim();
    if (!v) return;
    setQ(v);
    setRunning(true);
    setResults(null);
    // Synthetic streaming search — surfaces a few real traces after ~1.2s
    setTimeout(() => {
      setResults({
        query: v,
        hits: RECENT_TRACES.slice(0, 5).map((t, i) => ({
          ...t,
          score: 0.94 - i * 0.07,
          snippet:
            i === 0 ? "agent fell back to single-file reads after the bulk grep timed out"
              : i === 1 ? "context window hit 92% on a 47-step refactor; agent compacted reasoning twice"
              : i === 2 ? "Read/Edit oscillation on trail.jsx 13 times before convergence"
              : i === 3 ? "user paused and asked to slow down; agent dropped to plan-first mode"
              : "tests skipped on commit; SOP grader flagged this in the next sweep",
        })),
      });
      setRunning(false);
    }, 1200);
  };

  // Run a query handed in from the command palette (Spotlight mode).
  React.useEffect(() => {
    if (initialQuery && initialQuery.trim()) {
      run(initialQuery);
      onConsumeQuery && onConsumeQuery();
    }
    // eslint-disable-next-line
  }, [initialQuery]);

  return (
    <div className="landing landing-spotlight">
      <PageHero
        kicker="Conversation intelligence"
        title="Spotlight"
        subtitle="Ask anything about your traces. Spotlight scans bodies, reasoning, tool calls, and minimap shape with an LLM grader."
      />

      <div className="sp-stage">
        <form
          className="sp-search"
          onSubmit={(e) => { e.preventDefault(); run(); }}
        >
          <Icon name="search" size={16} className="sp-search-ico" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ask anything about your traces…"
            className="sp-search-input"
            spellCheck={false}
          />
          <button type="submit" className="sp-search-btn" disabled={!q.trim() || running}>
            {running ? "Searching…" : "Search"}
          </button>
        </form>

        <div className="sp-beta">
          <div className="sp-beta-tag">Beta</div>
          <div className="sp-beta-body">
            <div className="sp-beta-h">AI-graded search</div>
            <div className="sp-beta-s">Searches over the past 14 days. Typically responds in 10–90 seconds depending on trace volume.</div>
          </div>
        </div>

        {!results && !running && (
          <>
            <div className="sp-section-lbl">Try searching for</div>
            <div className="sp-chips">
              {SPOTLIGHT_SUGGESTIONS.map((s, i) => (
                <button key={i} className="sp-chip" onClick={() => run(s)}>{s}</button>
              ))}
            </div>

            <div className="sp-section-lbl">Recent searches</div>
            <div className="sp-recent">
              {SPOTLIGHT_RECENT.map((r, i) => (
                <button key={i} className="sp-recent-row" onClick={() => run(r.q)}>
                  <Icon name="clock" size={11} />
                  <span className="spr-q">{r.q}</span>
                  <span className="spr-when mono">{r.when}</span>
                  <span className="spr-hits mono">{r.hits} hits</span>
                </button>
              ))}
            </div>
          </>
        )}

        {running && (
          <div className="sp-loading">
            <div className="sp-spinner" />
            <div className="sp-loading-h">Reading your traces…</div>
            <div className="sp-loading-s mono">scanning 1.4k traces · 24 datasets · 6 projects</div>
          </div>
        )}

        {results && (
          <div className="sp-results">
            <header className="sp-results-h">
              <span className="sp-results-q mono">"{results.query}"</span>
              <span className="sp-results-meta">{results.hits.length} ranked results</span>
              <button className="sp-clear" onClick={() => { setResults(null); setQ(""); inputRef.current?.focus(); }}>Clear</button>
            </header>
            <div className="sp-result-list">
              {results.hits.map((t, i) => (
                <div key={i} className="sp-result">
                  <div className="spr-score mono">{(t.score * 100).toFixed(0)}</div>
                  <div className="spr-body">
                    <div className="spr-title">{t.title}</div>
                    <div className="spr-snippet">{t.snippet}</div>
                    <div className="spr-meta">
                      <AgentGlyph agent={t.agent} size={10} />
                      <span>{t.agent.name}</span>
                      <span className="dot-sep" />
                      <RepoLabel repo={t.repo} />
                      <span className="dot-sep" />
                      <span className="mono">{t.id}</span>
                      <span className="dot-sep" />
                      <span>{relTime(t.timestamp)}</span>
                    </div>
                  </div>
                  <div className="spr-fp">
                    <Fingerprint classes={t.fingerprint} height={16} gap={1} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

window.SpotlightPage = SpotlightPage;
