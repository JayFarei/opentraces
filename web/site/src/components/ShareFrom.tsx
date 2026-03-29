import SectionRule from "./SectionRule";

export default function ShareFrom() {
  return (
    <section>
      <SectionRule label="get started" />
      <div className="section-title">Start pushing traces in 60 seconds. Get something back.</div>

      {/* Two-column: left = how, right = why */}
      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 48, marginBottom: 0 }}>

        {/* Left: integration path as a vertical flow */}
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 16 }}>
            how to publish
          </div>

          {/* Step 1: Install */}
          <div style={{ display: "flex", gap: 16, marginBottom: 24 }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 20, fontWeight: 300, color: "var(--text-dim)", width: 28, flexShrink: 0, textAlign: "right" }}>1</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 500, color: "var(--text)", marginBottom: 4 }}>Install</div>
              <div style={{ background: "var(--bg-alt)", border: "1px solid var(--border)", padding: "8px 14px", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>
                <span style={{ color: "var(--text-dim)" }}>$ </span>pipx install opentraces
              </div>
              <div style={{ background: "var(--bg-alt)", border: "1px solid var(--border)", padding: "8px 14px", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>
                <span style={{ color: "var(--text-dim)" }}>$ </span>brew install JayFarei/opentraces/opentraces
              </div>
            </div>
          </div>

          {/* Step 2: Init */}
          <div style={{ display: "flex", gap: 16, marginBottom: 24 }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 20, fontWeight: 300, color: "var(--text-dim)", width: 28, flexShrink: 0, textAlign: "right" }}>2</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 500, color: "var(--text)", marginBottom: 4 }}>Init your project</div>
              <div style={{ background: "var(--bg-alt)", border: "1px solid var(--border)", padding: "8px 14px", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-secondary)" }}>
                <span style={{ color: "var(--text-dim)" }}>$ </span>opentraces init
              </div>
            </div>
          </div>

          {/* Step 3: Push */}
          <div style={{ display: "flex", gap: 16, marginBottom: 24 }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 20, fontWeight: 300, color: "var(--text-dim)", width: 28, flexShrink: 0, textAlign: "right" }}>3</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 500, color: "var(--text)", marginBottom: 4 }}>Push</div>
              <div style={{ background: "var(--bg-alt)", border: "1px solid var(--border)", padding: "8px 14px", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-secondary)" }}>
                <span style={{ color: "var(--text-dim)" }}>$ </span>opentraces push
              </div>
            </div>
          </div>

          {/* Ongoing: automate */}
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 16 }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 12 }}>
              then automate
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {[
                { name: "Claude Code Skill", cmd: "opentraces install-skill claude" },
                { name: "Git post-commit hook", cmd: "opentraces auth --install-hook" },
              ].map((item) => (
                <div key={item.name} style={{ border: "1px solid var(--border)", padding: "10px 12px", background: "var(--surface)" }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 500, color: "var(--text-secondary)", marginBottom: 4 }}>{item.name}</div>
                  <code style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)" }}>{item.cmd}</code>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right: what you get back */}
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 16 }}>
            what you get back
          </div>

          <div style={{ border: "1px solid var(--border)", marginBottom: 12 }}>
            <div style={{ padding: 20, borderBottom: "1px solid var(--border)" }}>
              <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 500, color: "var(--text)", marginBottom: 6 }}>
                Private datasets by default
              </div>
              <p style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>
                Every push creates a private HF dataset. Like a private repo on GitHub. Publish when you are ready, on your terms.
              </p>
            </div>
            <div style={{ padding: 20, borderBottom: "1px solid var(--border)" }}>
              <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 500, color: "var(--text)", marginBottom: 6 }}>
                Contributor analytics
              </div>
              <p style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>
                Cost per session, cache hit rate, tool usage patterns, success rate. Your personal dashboard across all projects.
              </p>
            </div>
            <div style={{ padding: 20 }}>
              <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 500, color: "var(--text)", marginBottom: 6 }}>
                Training signal for the commons
              </div>
              <p style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>
                Outcome signals for RL. Tool sequences for SFT. Sub-agent hierarchy for orchestration. Real workflows, not synthetic benchmarks.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
