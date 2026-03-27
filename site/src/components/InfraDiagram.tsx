import SectionRule from "./SectionRule";

export default function InfraDiagram() {
  return (
    <section>
      <SectionRule label="infrastructure" />
      <div className="section-title" style={{ textAlign: "center" }}>Architecture</div>
      <p className="section-sub" style={{ textAlign: "center", margin: "8px auto 40px" }}>
        Three layers. Every trace sanitized before it leaves your machine.
      </p>

      <div className="infra">
        <div><span className="infra-box hl">Agent Sessions</span></div>
        <div className="infra-connector">{"\u2502"}</div>
        <div style={{ fontSize: 10, color: "var(--text-dim)" }}>CLI / Hook / Skill</div>
        <div className="infra-connector">{"\u2502"}</div>
        <div className="infra-wide" style={{ borderColor: "var(--accent)" }}>
          <span style={{ color: "var(--accent)" }}>opentraces</span>
          <span style={{ color: "var(--text-dim)" }}> {"\u2500\u2500"} parse {"\u2192"} enrich {"\u2192"} sanitize {"\u2192"} publish</span>
        </div>
        <div className="infra-connector">{"\u2502"}</div>
        <div style={{ fontSize: 10, color: "var(--text-dim)" }}>Security Tier 1 / 2 / 3</div>
        <div className="infra-connector">{"\u2502"}</div>
        <div className="infra-row">
          {["Claude Code", "Codex", "Cursor", "Gemini CLI", "OpenCode", "Cline"].map((name) => (
            <span key={name} className="infra-box">{name}</span>
          ))}
        </div>
        <div className="infra-connector" style={{ marginTop: 8 }}>{"\u2502"}</div>
        <div><span className="infra-box" style={{ fontSize: 13 }}>{"\uD83E\uDD17"} Hugging Face Hub</span></div>
      </div>
    </section>
  );
}
