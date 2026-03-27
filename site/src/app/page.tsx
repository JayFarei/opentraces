import ThemeToggle from "@/components/ThemeToggle";
import Nav from "@/components/Nav";
import Hero from "@/components/Hero";
import Features from "@/components/Features";
import UseCases from "@/components/UseCases";
import Stats from "@/components/Stats";
import SecurityTiers from "@/components/SecurityTiers";
import InfraDiagram from "@/components/InfraDiagram";
import SchemaExplorer from "@/components/SchemaExplorer";
import GetStarted from "@/components/GetStarted";
import Footer from "@/components/Footer";
import SectionRule from "@/components/SectionRule";

export default function Home() {
  return (
    <>
      <ThemeToggle />
      <div className="container">
        <Nav />
        <Hero />
        <Features />
        <UseCases />
        <Stats />

        {/* Security Tiers Section */}
        <section>
          <SectionRule label="security" />
          <div className="section-title" style={{ fontSize: 18 }}>Security Tiers</div>
          <div style={{ height: 12 }} />
          <div style={{ maxWidth: 520 }}>
            <SecurityTiers />
          </div>
        </section>

        <InfraDiagram />
        <SchemaExplorer />
        <GetStarted />
        <Footer />
      </div>
    </>
  );
}
