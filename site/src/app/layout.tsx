import type { Metadata } from "next";
import { Space_Grotesk, IBM_Plex_Mono, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display-loaded",
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
});

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  variable: "--font-body-loaded",
  weight: ["300", "400", "500", "600"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono-loaded",
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "opentraces.ai - The Commons for Agent Traces",
  description:
    "Open-source CLI for crowdsourcing AI coding agent session traces as structured JSONL datasets on Hugging Face Hub. Three security tiers. Training-first schema.",
  keywords: ["agent traces", "training data", "hugging face", "open source", "SFT", "RLHF", "Claude Code", "Codex"],
  openGraph: {
    title: "opentraces.ai",
    description: "Your agent traces are training data.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      data-theme="dark"
      className={`${spaceGrotesk.variable} ${ibmPlexMono.variable} ${jetbrainsMono.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
