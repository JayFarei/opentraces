import SectionRule from "./SectionRule";

export default function About() {
  return (
    <section>
      <SectionRule label="about" />
      <div className="section-title">Why</div>
      <div style={{ maxWidth: 560 }}>
        <p style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.7, marginBottom: 16 }}>
          The code is the artifact. The trace is the source. When logic runs through an LLM, the output file isn&apos;t what you learn from — the sequence of decisions, tool calls, and reasoning paths that got there is.
        </p>
        <p style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.7 }}>
          open traces captures that record at line granularity, ties it to outcomes, and publishes it as open data. One schema for training, attribution, and research. No walled gardens.
        </p>
      </div>
    </section>
  );
}
