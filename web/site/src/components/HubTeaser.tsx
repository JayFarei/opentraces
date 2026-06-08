import Link from "next/link";
import SectionRule from "./SectionRule";
import HubWindow from "./HubWindow";

const shipped = [
  "trace viewer · conversation + trail tabs · minimap · context-waste + run-health signals",
  "vcs trails · every commit resolved back to its sessions",
  "capsules · shareable bounded slices of a run",
  "spotlight · bm25 + semantic search across the bucket",
  "⌘K command palette",
];

// Views that exist in the prototype but are not yet shipped in the CLI.
const preview = [
  "evals · scored rollouts",
  "alerts · standing reports over trace usage",
];

export default function HubTeaser() {
  return (
    <section>
      <SectionRule label="sneak peek" />
      <div className="section-title">Browse your traces in the Hub, not just the terminal.</div>
      <p className="section-sub" style={{ maxWidth: 640, marginBottom: 24 }}>
        The Hub is the desktop companion to the CLI, where the bucket, trails, context tree, and
        datasets become browsable. Open a trace, read the conversation, follow the trail, watch the
        run health, all without leaving the page.
      </p>

      <div className="hub-teaser-wide">
        <HubWindow clipHeight={680} lazy address="opentraces.ai/hub" />
      </div>

      <div className="hub-teaser-foot">
        <div className="hub-teaser-cols">
          <ul className="hub-teaser-bullets">
            {shipped.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
          <div className="hub-teaser-preview">
            <div className="hub-teaser-preview-label">in the prototype · not yet in the cli</div>
            <ul className="hub-teaser-bullets muted">
              {preview.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </div>
        </div>
        <Link className="btn btn-primary hub-teaser-cta" href="/hub">[open the hub]</Link>
      </div>
    </section>
  );
}
