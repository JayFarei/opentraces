import SectionRule from "./SectionRule";

export default function TrainingValue() {
  return (
    <section>
      <SectionRule label="why share" />
      <div className="section-title">Your traces are more valuable than you think.</div>
      <p className="section-sub" style={{ maxWidth: 560 }}>
        Real developer workflows are the scarcest training signal. Every trace you publish helps the next generation of coding agents get better.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1px", background: "var(--border)", border: "1px solid var(--border)", marginBottom: 32 }}>
        {[
          {
            label: "FOR YOU",
            title: "Free analytics dashboard",
            desc: "Your Spotify Wrapped for coding agents. Cost per session, cache hit rate, tool usage patterns, success rate. Published data powers your personal dashboard on HF.",
          },
          {
            label: "FOR TRAINING",
            title: "SFT + RL data",
            desc: "Outcome signals for reward modeling. Tool call sequences for SFT. Sub-agent hierarchy for orchestration training. Real workflows, not synthetic benchmarks.",
          },
          {
            label: "FOR RESEARCH",
            title: "Study agent behavior",
            desc: "How do developers use agents? Where are tokens wasted? Which tools are overused? The data answers questions no synthetic benchmark can.",
          },
          {
            label: "FOR THE COMMONS",
            title: "Open data on HF Hub",
            desc: "Every trace lands on Hugging Face Hub under your name. Loadable with one line: datasets.load_dataset(). No proprietary lock-in, no walled garden.",
          },
        ].map((item) => (
          <div key={item.label} style={{ background: "var(--bg)", padding: 24 }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, letterSpacing: "0.12em", color: "var(--accent)", marginBottom: 8 }}>{item.label}</div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 500, color: "var(--text)", marginBottom: 8 }}>{item.title}</div>
            <p style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>{item.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
