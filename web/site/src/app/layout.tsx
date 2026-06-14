import type { Metadata } from "next";
import { Geist, Geist_Mono, Space_Grotesk } from "next/font/google";
import "./globals.css";
import WebMcpTools from "@/components/WebMcpTools";

// OpenTraces design system: Geist (body), Geist Mono (metadata), Space Grotesk (display).
const geist = Geist({
  subsets: ["latin"],
  variable: "--font-body-loaded",
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono-loaded",
  weight: ["400", "500"],
  display: "swap",
});

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display-loaded",
  weight: ["300", "400", "500", "600", "700"],
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
      className={`${geist.variable} ${geistMono.variable} ${spaceGrotesk.variable}`}
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var t=localStorage.getItem('theme');if(!t){t=window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'}var d=document.documentElement;d.setAttribute('data-theme',t);d.classList.add(t==='dark'?'theme-dark':'theme-light');d.style.colorScheme=t;function tog(){var n=d.getAttribute('data-theme')==='dark'?'light':'dark';d.setAttribute('data-theme',n);d.classList.remove('theme-dark','theme-light');d.classList.add(n==='dark'?'theme-dark':'theme-light');d.style.colorScheme=n;localStorage.setItem('theme',n)}document.addEventListener('DOMContentLoaded',function(){var b=document.querySelector('.nav-theme-btn');if(!b)return;b.addEventListener('click',tog);new MutationObserver(function(_,o){if(b.textContent&&b.textContent.trim()){b.removeEventListener('click',tog);o.disconnect()}}).observe(b,{childList:true,subtree:true,characterData:true})})})()`,
          }}
        />
        <script defer id="analytics" data-site-id="opentraces.ai" src="https://lazyanalytics.fareiunastrage.workers.dev/tracker.js" />
      </head>
      <body>
        <WebMcpTools />
        {children}
      </body>
    </html>
  );
}
