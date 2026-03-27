export default function GetStarted() {
  return (
    <div className="cta-section">
      <div className="section-title" style={{ marginBottom: 12 }}>Get started</div>
      <p className="section-sub" style={{ textAlign: "center", margin: "0 auto 24px" }}>
        One pip install. One command. Every trace contributes to the open training commons.
      </p>
      <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
        <a className="btn btn-primary" href="#">[start contributing]</a>
        <a className="btn btn-outline" href="/docs/">[documentation]</a>
      </div>
    </div>
  );
}
