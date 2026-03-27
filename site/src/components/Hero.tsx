import Terminal from "./Terminal";

export default function Hero() {
  return (
    <section className="hero">
      <div className="hero-grid">
        <div>
          <div className="hero-pill">open traces &nbsp; v0.1.0</div>
          <div style={{ height: 16 }} />
          <h1>Your agent traces are training data.</h1>
          <p className="hero-sub">
            Open-source CLI that publishes coding agent sessions as structured JSONL on Hugging Face Hub. Three security tiers. Training-first schema.
          </p>
          <div className="hero-cli-wrap">
            <span className="hero-cli-prefix">$</span>
            <span className="hero-cli-input">pip install opentraces</span>
            <span className="hero-cli-copy" title="Copy">[cp]</span>
          </div>
          <div className="hero-actions">
            <a className="btn btn-primary" href="#">Start contributing</a>
            <a className="btn btn-outline" href="/docs/">Documentation</a>
          </div>
        </div>
        <div>
          <Terminal
            tabs={[
              { label: "publish", active: true },
              { label: "review" },
              { label: "status" },
            ]}
            title="opentraces — zsh"
          >
            <span className="terminal-line"><span className="p">~$</span> <span className="c">opentraces publish</span> <span className="f">--tier</span> <span className="s">automated</span></span>
            <span className="terminal-line terminal-line-gap" />
            <span className="terminal-line"><span className="di">scanning ~/.claude/projects ...</span></span>
            <span className="terminal-line"><span className="di">found</span> <span className="n">23</span> <span className="di">sessions across</span> <span className="n">4</span> <span className="di">projects</span></span>
            <span className="terminal-line terminal-line-gap" />
            <span className="terminal-line"><span className="di">{"\u251C\u2500"} security tier:</span>   <span className="s">automated</span></span>
            <span className="terminal-line"><span className="di">{"\u251C\u2500"} auto-redacted:</span>   <span className="n">3</span> <span className="di">secrets</span></span>
            <span className="terminal-line"><span className="di">{"\u2514\u2500"} flagged:</span>         <span className="w">1</span> <span className="di">session</span></span>
            <span className="terminal-line terminal-line-gap" />
            <span className="terminal-line"><span className="ok">{"\u2713"}</span> <span className="di">published</span> <span className="n">22</span> <span className="di">traces {"\u2192"}</span> <span className="s">jayfarei/opentraces-claude</span></span>
            <span className="terminal-line"><span className="di">  {"\u21B3"} 1 pending review. run</span> <span className="c">opentraces review</span></span>
          </Terminal>
        </div>
      </div>
    </section>
  );
}
