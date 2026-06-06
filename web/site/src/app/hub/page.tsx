import type { Metadata } from "next";
import Link from "next/link";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import HubWindow from "@/components/HubWindow";

export const metadata: Metadata = {
  title: "OpenTraces Hub — interactive preview",
  description:
    "A live, interactive preview of the OpenTraces Hub: the desktop companion to the CLI where the bucket, trails, context tree, and datasets become browsable.",
};

export default function HubPage() {
  return (
    <div className="container hub-page-container">
      <Nav />
      <section className="hub-page">
        <div className="hub-page-head">
          <div>
            <div className="hub-page-eyebrow">sneak peek</div>
            <h1 className="hub-page-title">
              open<strong>traces</strong> hub
            </h1>
          </div>
          <Link href="/" className="hub-page-back">← back home</Link>
        </div>
        <p className="hub-page-sub">
          The desktop companion to the CLI. The bucket, trails, context tree, and datasets become
          browsable. This is the real prototype, running in your browser. Use the sidebar to move
          between views, toggle the theme, and press ⌘K inside the window for the command palette.
        </p>
        <HubWindow address="opentraces.ai/hub" />
        <p className="hub-page-note">
          interactive prototype · runs entirely client-side · no data leaves your machine
        </p>
      </section>
      <Footer />
    </div>
  );
}
