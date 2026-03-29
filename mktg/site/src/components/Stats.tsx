import SectionRule from "./SectionRule";

const stats = [
  { label: "traces", value: "847", delta: "+34 this week", dir: "up" as const },
  { label: "success rate", value: "72%", delta: "+4.2% vs last mo", dir: "up" as const },
  { label: "avg cost", value: "$1.84", delta: "+$0.12 vs community", dir: "dn" as const },
  { label: "cache hit", value: "89%", delta: "top 15%", dir: "up" as const },
];

const sessions = [
  { id: "a4f2b8c1", agent: "claude-code", steps: 42, cost: "$2.40", outcome: "success", outcomeClass: "badge-ok", tier: "review", tierClass: "badge-ac" },
  { id: "b8c1d9e3", agent: "claude-code", steps: 18, cost: "$0.87", outcome: "failed", outcomeClass: "badge-er", tier: "review", tierClass: "badge-ac" },
  { id: "d9e3f1a2", agent: "codex", steps: 31, cost: "$1.92", outcome: "success", outcomeClass: "badge-ok", tier: "auto", tierClass: "badge-wa" },
  { id: "f1a2c3b4", agent: "gemini-cli", steps: 7, cost: "$0.34", outcome: "flagged", outcomeClass: "badge-wa", tier: "review", tierClass: "badge-bl" },
];

const tools = [
  { name: "bash", pct: 92 },
  { name: "edit", pct: 78 },
  { name: "read", pct: 65 },
  { name: "grep", pct: 48 },
  { name: "glob", pct: 35 },
  { name: "write", pct: 22 },
  { name: "agent", pct: 12 },
];

const models = [
  { name: "claude-sonnet-4", pct: "62%" },
  { name: "claude-opus-4", pct: "24%" },
  { name: "gpt-4.1", pct: "8%" },
  { name: "gemini-2.5-pro", pct: "6%" },
];

export default function Stats() {
  return (
    <section>
      <SectionRule label="dashboard" />

      <div className="stats-row" style={{ marginBottom: 16 }}>
        {stats.map((s) => (
          <div key={s.label} className="stat-cell">
            <div className="stat-label">{s.label}</div>
            <div className="stat-value">{s.value}</div>
            <div className={`stat-delta ${s.dir}`}>{s.delta}</div>
          </div>
        ))}
      </div>

      <div className="tbl-wrap" style={{ marginBottom: 16 }}>
        <div className="tbl-head">
          <span className="tbl-title">recent sessions</span>
          <span className="btn btn-outline btn-sm">[export]</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>session</th>
              <th>agent</th>
              <th>steps</th>
              <th>cost</th>
              <th>outcome</th>
              <th>tier</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.id}>
                <td>{s.id}</td>
                <td>{s.agent}</td>
                <td>{s.steps}</td>
                <td>{s.cost}</td>
                <td><span className={`badge ${s.outcomeClass}`}>{s.outcome}</span></td>
                <td><span className={`badge ${s.tierClass}`}>{s.tier}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div style={{ border: "1px solid var(--border)", padding: 16, background: "var(--surface)" }}>
          <div className="stat-label" style={{ marginBottom: 12 }}>tool usage</div>
          <div className="ascii-bars" style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
            {tools.map((t) => (
              <div key={t.name} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                <span style={{ width: 48, textAlign: "right", color: "var(--text-muted)", fontSize: 11, flexShrink: 0 }}>{t.name}</span>
                <div style={{ flex: 1, height: 14, background: "var(--surface)", border: "1px solid var(--border)", position: "relative", overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${t.pct}%`, background: "var(--accent-bg)", borderRight: "2px solid var(--accent)" }} />
                </div>
                <span style={{ fontSize: 10, color: "var(--text-dim)", width: 32, flexShrink: 0 }}>{t.pct}%</span>
              </div>
            ))}
          </div>
        </div>
        <div style={{ border: "1px solid var(--border)", padding: 16, background: "var(--surface)" }}>
          <div className="stat-label" style={{ marginBottom: 12 }}>model distribution</div>
          <table style={{ fontSize: 12 }}>
            <tbody>
              {models.map((m) => (
                <tr key={m.name}>
                  <td style={{ color: "var(--text-muted)", padding: "4px 8px", border: "none" }}>{m.name}</td>
                  <td style={{ textAlign: "right", padding: "4px 8px", border: "none", color: "var(--text-secondary)" }}>{m.pct}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
