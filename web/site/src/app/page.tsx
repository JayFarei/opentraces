import type { Metadata } from "next";
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
import JsonLd from "@/components/JsonLd";
import { getHomepageHeroMetrics } from "@/lib/homepage-metrics";
import { AGENT_PROMPT } from "@/lib/agent-prompt";
import { homeStructuredData } from "@/lib/structured-data";

export const metadata: Metadata = {
  alternates: { canonical: "https://opentraces.ai/" },
};

export default async function Home() {
  const { metrics, stars, installSeries } = await getHomepageHeroMetrics();

  return (
    <>
      <JsonLd data={homeStructuredData} />
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
