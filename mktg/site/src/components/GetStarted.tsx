import SectionRule from "./SectionRule";

export default function GetStarted() {
  return (
    <section>
      <SectionRule label="get started" />
      <div className="section-title">Three commands. That&apos;s it.</div>
      <p className="section-sub">
        Open data is the new open source. Your agent traces are the most valuable dataset
        nobody is collecting. Start contributing to the commons.
      </p>
      <div className="get-started-steps">
        <div className="get-started-step">
          <code>opentraces init</code>
          <span>opt in your project, pick auto or review mode</span>
        </div>
        <div className="get-started-step">
          <code>opentraces review</code>
          <span>inspect traces in the TUI before sharing</span>
        </div>
        <div className="get-started-step">
          <code>opentraces push</code>
          <span>publish to your HF dataset, private or public</span>
        </div>
      </div>
      <div className="hero-actions" style={{ marginTop: 32 }}>
        <a className="btn btn-primary" href="#">[get started]</a>
        <a className="btn btn-outline" href="/docs/">[documentation]</a>
      </div>
    </section>
  );
}
