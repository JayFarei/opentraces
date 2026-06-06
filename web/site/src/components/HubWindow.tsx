"use client";

import { useEffect, useRef, useState } from "react";

// The Hub prototype is a fixed-desktop layout (260px sidebar, no narrow
// breakpoints). Render it at a constant logical canvas and CSS-scale the whole
// iframe to fit the column, so it stays crisp and never reflows.
const CANVAS_W = 1280;
const CANVAS_H = 800;
// Below this rendered width the scaled app is unreadable, so fall back to the poster.
const MIN_WIDTH = 600;

type Theme = "light" | "dark";

function readSiteTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

// Push a theme into the (same-origin) Hub iframe so it matches the site.
function applyThemeToIframe(frame: HTMLIFrameElement | null, theme: Theme) {
  if (!frame) return;
  try {
    const doc = frame.contentDocument;
    if (doc?.documentElement) {
      const el = doc.documentElement;
      el.setAttribute("data-theme", theme);
      el.classList.remove("theme-dark", "theme-light");
      el.classList.add(theme === "dark" ? "theme-dark" : "theme-light");
      el.style.colorScheme = theme;
    }
    frame.contentWindow?.localStorage?.setItem("ot-theme", theme);
  } catch {
    /* cross-origin or not ready yet — ignore */
  }
}

interface HubWindowProps {
  src?: string;
  /** Override the poster; otherwise the theme-matched poster is used. */
  poster?: string;
  /** Clip the visible viewport to this many CSS px (teaser peek). Omit for full height. */
  clipHeight?: number;
  /** Defer mounting the live iframe until scrolled near. */
  lazy?: boolean;
  address?: string;
}

export default function HubWindow({
  src = "/hub-preview/index.html",
  poster,
  clipHeight,
  lazy = false,
  address = "opentraces.ai/hub",
}: HubWindowProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [scale, setScale] = useState(0);
  const [mounted, setMounted] = useState(!lazy);
  const [loaded, setLoaded] = useState(false);
  const [theme, setTheme] = useState<Theme>(readSiteTheme);

  // Seed the Hub's boot theme BEFORE the iframe loads (it reads ot-theme on boot),
  // so it comes up matching the site rather than flashing its dark default.
  if (typeof window !== "undefined") {
    try { window.localStorage.setItem("ot-theme", theme); } catch { /* ignore */ }
  }

  // Measure the viewport width → scale factor. Width is set by the column and
  // is independent of the height we set, so this never feedback-loops.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth;
      if (w > 0) setScale(w / CANVAS_W);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Lazy-mount the (heavy, Babel-compiled) iframe only when it scrolls near.
  useEffect(() => {
    if (mounted) return;
    const el = viewportRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setMounted(true);
          io.disconnect();
        }
      },
      { rootMargin: "400px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [mounted]);

  // Follow the site theme: sync on every toggle of <html data-theme>.
  useEffect(() => {
    const html = document.documentElement;
    const sync = () => {
      const t = readSiteTheme();
      setTheme(t);
      try { window.localStorage.setItem("ot-theme", t); } catch { /* ignore */ }
      applyThemeToIframe(iframeRef.current, t);
    };
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(html, { attributes: true, attributeFilter: ["data-theme", "class"] });
    return () => obs.disconnect();
  }, []);

  const renderedWidth = scale * CANVAS_W;
  const tooNarrow = scale > 0 && renderedWidth < MIN_WIDTH;
  const fullHeight = CANVAS_H * scale;
  const viewportHeight = scale > 0
    ? (clipHeight ? Math.min(fullHeight, clipHeight) : fullHeight)
    : (clipHeight ?? 480);

  const posterSrc = poster ?? (theme === "light" ? "/hub-poster-light.png" : "/hub-poster.png");

  return (
    <div className="hub-window">
      <div className="hub-window-bar">
        <div className="hub-window-lights" aria-hidden="true">
          <span className="hub-light hub-light-r" />
          <span className="hub-light hub-light-y" />
          <span className="hub-light hub-light-g" />
        </div>
        <div className="hub-window-title">opentraces hub</div>
        <div className="hub-window-addr">{address}</div>
      </div>

      <div
        className="hub-window-viewport"
        ref={viewportRef}
        style={{ height: viewportHeight }}
        data-clip={clipHeight ? "1" : "0"}
      >
        {/* Poster sits underneath: it is the loading state, and the mobile fallback. */}
        <img
          className={`hub-window-poster${loaded && !tooNarrow ? " hidden" : ""}`}
          src={posterSrc}
          alt="OpenTraces Hub — workspace overview"
          aria-hidden={loaded ? "true" : undefined}
        />

        {tooNarrow && (
          <div className="hub-window-narrow">best viewed on desktop</div>
        )}

        {!tooNarrow && mounted && scale > 0 && (
          <iframe
            ref={iframeRef}
            className={`hub-window-frame${loaded ? " loaded" : ""}`}
            src={src}
            title="OpenTraces Hub interactive preview"
            loading={lazy ? "lazy" : "eager"}
            onLoad={() => { setLoaded(true); applyThemeToIframe(iframeRef.current, theme); }}
            style={{
              width: CANVAS_W,
              height: CANVAS_H,
              transform: `scale(${scale})`,
              transformOrigin: "top left",
            }}
          />
        )}

        {clipHeight ? <div className="hub-window-fade" /> : null}
      </div>
    </div>
  );
}
