import SectionRule from "./SectionRule";

export default function About() {
  return (
    <section>
      <SectionRule label="about" />
      <div className="section-title">Why</div>
      <div style={{ maxWidth: 560 }}>
        <p style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.7, marginBottom: 16 }}>
          Every commit discards the reasoning that produced the code. Capture tools exist, but none publish to open datasets. Sharing tools exist, but lock data in walled gardens.
        </p>
        <p style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.7 }}>
          open traces connects the full conversation trajectory to the specific code output at line granularity. Process + output, unified. One schema for training, attribution, and research.
        </p>
      </div>
    </section>
  );
}
