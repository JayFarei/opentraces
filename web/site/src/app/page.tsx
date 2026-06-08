import Nav from "@/components/Nav";
import Hero from "@/components/Hero";
import Features from "@/components/Features";
import HubTeaser from "@/components/HubTeaser";
import PrivacyTrust from "@/components/PrivacyTrust";
import Attribution from "@/components/Attribution";
import InfraDiagram from "@/components/InfraDiagram";
import GetStarted from "@/components/GetStarted";
import Footer from "@/components/Footer";
import StarCallout from "@/components/StarCallout";
import { getHomepageHeroMetrics } from "@/lib/homepage-metrics";
import { AGENT_PROMPT } from "@/lib/agent-prompt";

export default async function Home() {
  const { metrics, stars, installSeries } = await getHomepageHeroMetrics();

  return (
    <>
      <div className="container">
        <Nav stars={stars} />
        <Hero metrics={metrics} agentPrompt={AGENT_PROMPT} installSeries={installSeries} />
        <HubTeaser />
        <Features />
        <PrivacyTrust />
        <Attribution />
        <InfraDiagram />
        <GetStarted agentPrompt={AGENT_PROMPT} />
        <Footer />
      </div>
      <StarCallout />
    </>
  );
}
