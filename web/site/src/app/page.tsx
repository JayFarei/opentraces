import Nav from "@/components/Nav";
import Hero from "@/components/Hero";
import Features from "@/components/Features";
import PrivacyTrust from "@/components/PrivacyTrust";
import InfraDiagram from "@/components/InfraDiagram";
import SchemaExplorer from "@/components/SchemaExplorer";
import GetStarted from "@/components/GetStarted";
import Footer from "@/components/Footer";
import StarCallout from "@/components/StarCallout";
import { getHomepageHeroMetrics } from "@/lib/homepage-metrics";

export default async function Home() {
  const { metrics, stars } = await getHomepageHeroMetrics();

  return (
    <>
      <div className="container">
        <Nav stars={stars} />
        <Hero metrics={metrics} />
        <Features />
        <PrivacyTrust />
        <InfraDiagram />
        <SchemaExplorer />
        <GetStarted />
        <Footer />
      </div>
      <StarCallout />
    </>
  );
}
