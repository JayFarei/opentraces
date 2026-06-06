import Link from "next/link";
import SectionRule from "./SectionRule";
import HubWindow from "./HubWindow";

const bullets = [
  "trace viewer · conversation + trail tabs · minimap · context-waste + run-health signals",
  "vcs trails · every commit resolved back to its sessions",
  "capsules · shareable bounded slices of a run",
  "spotlight · search across the bucket, bm25 + semantic",
  "evals, alerts, and a ⌘K command palette",
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

      <HubWindow clipHeight={460} lazy address="opentraces.ai/hub" />

      <div className="hub-teaser-foot">
        <ul className="hub-teaser-bullets">
          {bullets.map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
        <Link className="btn btn-primary hub-teaser-cta" href="/hub">[open the hub]</Link>
      </div>
    </section>
  );
}
