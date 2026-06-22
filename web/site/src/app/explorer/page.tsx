import type { Metadata } from "next";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import Dashboard from "@/components/Dashboard";
import JsonLd from "@/components/JsonLd";
import { explorerStructuredData } from "@/lib/structured-data";

export const metadata: Metadata = {
  title: "Explorer — opentraces",
  description:
    "Browse live opentraces trace datasets on Hugging Face Hub: dataset stats, sample trace rows, and agent, model, and dependency breakdowns.",
  alternates: { canonical: "https://opentraces.ai/explorer" },
  openGraph: {
    title: "Explorer — opentraces",
    description:
      "Browse live opentraces trace datasets on Hugging Face Hub: dataset stats, sample trace rows, and agent, model, and dependency breakdowns.",
    url: "https://opentraces.ai/explorer",
    type: "website",
  },
};

export default function ExplorerPage() {
  return (
    <>
      <JsonLd data={explorerStructuredData} />
      <div className="container">
        <Nav />
        <Dashboard />
        <Footer />
      </div>
    </>
  );
}
