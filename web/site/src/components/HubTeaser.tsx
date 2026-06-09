import Link from "next/link";
import SectionRule from "./SectionRule";
import HubWindow from "./HubWindow";
import EarlyAccessForm from "./EarlyAccessForm";

export default function HubTeaser() {
  return (
    <section>
      <SectionRule label="hub preview" pill="new" />
      <div className="section-title">Learn from your team data.</div>
      <p className="section-sub" style={{ maxWidth: 640, marginBottom: 24 }}>
        Every run your team makes is data you can learn from. The Hub is the web companion to the
        CLI, where your shared bucket, trails, context tree, and datasets become browsable, with
        trace intelligence on top.
      </p>

      <div className="hub-teaser-wide">
        <HubWindow clipHeight={680} lazy address="opentraces.ai/hub" />
        <Link className="hub-peel" href="/hub" aria-label="Learn more about the hub">
          <span className="hub-peel-label">learn more about the hub</span>
          <span className="hub-peel-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M7 7h10v10" />
              <path d="M7 17 17 7" />
            </svg>
          </span>
        </Link>
      </div>

      <div className="hub-teaser-foot">
        <p className="hub-teaser-pitch">
          Get early access for you and your team — a 15min call with the founder to get you set
          up, plus unlimited trace intelligence credits while the Hub is in preview.
        </p>
        <div className="hub-teaser-cta-col">
          <EarlyAccessForm />
        </div>
      </div>
    </section>
  );
}
