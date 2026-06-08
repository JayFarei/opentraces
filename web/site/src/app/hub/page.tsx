import type { Metadata } from "next";
import Link from "next/link";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import HubWindow from "@/components/HubWindow";
import HubFeatureFrame from "@/components/HubFeatureFrame";
import HubTourNav from "@/components/HubTourNav";
import EarlyAccessForm from "@/components/EarlyAccessForm";
import { HUB_FEATURE_GROUPS, type HubFeature } from "@/lib/hub-features";

export const metadata: Metadata = {
  title: "OpenTraces Hub: interactive preview",
  description:
    "An interactive preview of the OpenTraces Hub: the web companion to the CLI where your team's shared bucket, trails, context tree, and datasets become browsable, with trace intelligence on top.",
};

function FeatureCard({ feature }: { feature: HubFeature }) {
  return (
    <article className="hub-feature" id={`feature-${feature.id}`}>
      <div className="hub-feature-copy">
        <div className="hub-feature-text">
          <div className="hub-feature-kicker">{feature.kicker}</div>
          <h3 className="hub-feature-heading">{feature.heading}</h3>
          <p className="hub-feature-body">{feature.body}</p>
        </div>
        {feature.bullets.length > 0 && (
          <ul className="hub-feature-bullets">
            {feature.bullets.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        )}
      </div>
      <div className="hub-feature-stage">
        <HubFeatureFrame
          view={feature.view}
          repo={feature.repo}
          dataset={feature.dataset}
          child={feature.child}
          tab={feature.tab}
          pr={feature.pr}
          height={feature.height}
          label={feature.heading}
        />
      </div>
    </article>
  );
}

function HubCta({ size = "default" }: { size?: "default" | "large" }) {
  return (
    <section className="hub-cta" data-size={size}>
      <div className="hub-cta-inner">
        <div className="hub-cta-eyebrow">private alpha</div>
        <h2 className="hub-cta-title">Get your team into the Hub.</h2>
        <p className="hub-cta-body">
          Get early access for you and your team, a 15-minute call with the founder to get you set
          up, plus unlimited trace intelligence credits while the Hub is in preview.
        </p>
        <EarlyAccessForm />
      </div>
    </section>
  );
}

export default function HubPage() {
  return (
    <div className="container hub-page-container">
      <Nav />
      <section className="hub-page">
        <div className="hub-page-head">
          <div>
            <div className="hub-page-eyebrow">sneak peek</div>
            <h1 className="hub-page-title hub-wordmark">
              <span className="hub-wm-open">open</span>
              <span className="hub-wm-traces">traces</span>
              <span className="hub-wm-tag">Hub</span>
            </h1>
          </div>
          <Link href="/" className="hub-page-back">← back home</Link>
        </div>
        <p className="hub-page-sub">
          The web companion to the CLI. Your team&apos;s shared bucket, trails, context tree, and
          datasets become browsable. This is the real prototype, running in your browser. Drive the
          whole thing below, or scroll for a page-by-page tour of every view.
        </p>
        <div className="hub-hero-window">
          <HubWindow address="opentraces.ai/hub" />
        </div>
        <p className="hub-page-note">interactive prototype</p>
      </section>

      <p className="hub-mobile-note">
        The live Hub preview is built for desktop. Come back on a larger screen to drive it — for
        now, here&apos;s what each view does.
      </p>

      <section className="hub-tour">
        <HubCta size="default" />

        <div className="hub-tour-lead">
          <div className="hub-tour-eyebrow">preview</div>
          <h2 className="hub-tour-title">An interactive preview</h2>
          <p className="hub-tour-sub">
            A live look at what we&apos;re building into the Hub. Every panel below is the real view,
            booted on its own, not a screenshot.
          </p>
        </div>

        <div className="hub-tour-body">
          <HubTourNav />
          <div className="hub-tour-content">
            {HUB_FEATURE_GROUPS.map((group) => (
              <div className="hub-group" key={group.id}>
                <div className="hub-group-head">
                  <span className="hub-group-label">{group.label}</span>
                  <span className="hub-group-blurb">{group.blurb}</span>
                </div>
                <div className="hub-feature-list">
                  {group.features.map((f) => (
                    <FeatureCard key={f.id} feature={f} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <HubCta size="large" />

      <Footer />
    </div>
  );
}
