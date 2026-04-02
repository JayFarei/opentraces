import Nav from "@/components/Nav";
import Hero from "@/components/Hero";
import Features from "@/components/Features";
import PrivacyTrust from "@/components/PrivacyTrust";
import InfraDiagram from "@/components/InfraDiagram";
import SchemaExplorer from "@/components/SchemaExplorer";
import GetStarted from "@/components/GetStarted";
import Footer from "@/components/Footer";
import { getHomepageHeroMetrics } from "@/lib/homepage-metrics";

export default async function Home() {
  const heroMetrics = await getHomepageHeroMetrics();

  return (
    <>
      <div className="container">
        <Nav />
        <Hero metrics={heroMetrics} />
        <Features />
        <PrivacyTrust />
        <InfraDiagram />
        <SchemaExplorer />
        <GetStarted />
        <Footer />
      </div>
    </>
  );
}
