import Nav from "@/components/Nav";
import Hero from "@/components/Hero";
import Features from "@/components/Features";
import PrivacyTrust from "@/components/PrivacyTrust";
import InfraDiagram from "@/components/InfraDiagram";
import SchemaExplorer from "@/components/SchemaExplorer";
import GetStarted from "@/components/GetStarted";
import Footer from "@/components/Footer";
export default function Home() {
  return (
    <>
      <div className="container">
        <Nav />
        <Hero />
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
