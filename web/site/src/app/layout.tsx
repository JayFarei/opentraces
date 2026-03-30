import type { Metadata } from "next";
import { Space_Grotesk, IBM_Plex_Mono, JetBrains_Mono, Space_Mono } from "next/font/google";
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

const spaceMono = Space_Mono({
  subsets: ["latin"],
  variable: "--font-label-loaded",
  weight: ["400", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://opentraces.ai"),
  title: "open traces - The Commons for Agent Traces",
  description:
    "Open-source CLI for crowdsourcing AI coding agent session traces as structured JSONL datasets on Hugging Face Hub. Auto or review mode. Training-first schema.",
  keywords: ["agent traces", "training data", "hugging face", "open source", "SFT", "RLHF", "Claude Code", "Codex"],
  openGraph: {
    title: "open traces",
    description: "Your agent traces are training data. Open protocol for crowdsourcing AI agent session traces.",
    type: "website",
    url: "https://opentraces.ai",
    images: [{ url: "/og.png", width: 1200, height: 630 }],
  },
  twitter: {
    card: "summary_large_image",
    title: "open traces",
    description: "Your agent traces are training data. Open protocol for crowdsourcing AI agent session traces.",
    images: ["/og.png"],
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
      suppressHydrationWarning
      className={`${spaceGrotesk.variable} ${ibmPlexMono.variable} ${jetbrainsMono.variable} ${spaceMono.variable}`}
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var t=localStorage.getItem('theme');if(!t){t=window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'}var d=document.documentElement;d.setAttribute('data-theme',t);d.classList.add(t==='dark'?'theme-dark':'theme-light');d.style.colorScheme=t})()`,
          }}
        />
        <script data-site-id="opentraces.ai" src="https://analytics-agent.fareiunastrage.workers.dev/tracker.js" async />
      </head>
      <body>{children}</body>
    </html>
  );
}
