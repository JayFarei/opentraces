"use client";

import { useEffect, useRef, useState } from "react";

// One chromeless feature view of the Hub prototype, embedded as a scaled,
// same-origin iframe. Each card on the /hub showcase renders the REAL view via
// ?embed=1&view=… so the breakdown is the live product, not screenshots.
//
// The page can stack ~12 of these, and every iframe boots React + Babel, so we
// (a) lazy-mount only when scrolled near and (b) UNMOUNT when scrolled far
// (windowing) — keeping at most a handful of live iframes alive at once. A
// skeleton stands in while a frame is parked.

// Logical canvas width = the real Hub main-column width (1280 − 260px sidebar),
// so the chromeless content renders pixel-faithfully with zero reflow, then the
// whole panel is CSS-scaled to fit the (TOC-narrowed) tour column.
// A view denser than this (e.g. the capsules table, ~1212px) can override it via
// `canvasWidth` so it renders at its natural width and scales down uniformly
// instead of clipping its right-hand columns inside the narrower default canvas.
const CANVAS_W = 1020;
// Below this rendered width the scaled app is unreadable → show the mobile note.
const MIN_WIDTH = 520;

type Theme = "light" | "dark";

function readSiteTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

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
    /* not ready yet — ignore */
  }
}

// ── Boot concurrency gate ────────────────────────────────────────────────
// Every iframe boots React + Babel (a CPU spike while Babel compiles the Hub's
// JSX). Without a gate, scrolling can kick off several boots in the same frame
// and jank the main thread. We cap how many iframes may be *booting* at once;
// a frame releases its slot on load, so already-rendered frames cost nothing.
const MAX_CONCURRENT_BOOTS = 2;
let activeBoots = 0;
const bootQueue: Array<() => void> = [];

function acquireBoot(): Promise<void> {
  return new Promise((resolve) => {
    if (activeBoots < MAX_CONCURRENT_BOOTS) {
      activeBoots += 1;
      resolve();
    } else {
      bootQueue.push(resolve);
    }
  });
}

function releaseBoot() {
  const next = bootQueue.shift();
  if (next) {
    next(); // hand the slot straight to the next waiter
  } else {
    activeBoots = Math.max(0, activeBoots - 1);
  }
}

export interface HubFeatureFrameProps {
  /** view id passed to the Hub app (?view=…). */
  view: string;
  repo?: string;
  dataset?: string;
  child?: string;
  tab?: "conversation" | "trail";
  trace?: string;
  /** Open straight into a PR detail (pulls view). */
  pr?: string;
  /** Logical canvas height (px) for this feature — tuned per view. */
  height: number;
  /** Logical canvas width (px) override for dense views — defaults to CANVAS_W. */
  canvasWidth?: number;
  /** Accessible label for the live region. */
  label: string;
}

export default function HubFeatureFrame({
  view,
  repo,
  dataset,
  child,
  tab,
  trace,
  pr,
  height,
  canvasWidth,
  label,
}: HubFeatureFrameProps) {
  const canvasW = canvasWidth ?? CANVAS_W;
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [scale, setScale] = useState(0);
  const [mounted, setMounted] = useState(false);
  const [hasSlot, setHasSlot] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [theme, setTheme] = useState<Theme>("light");
  const slotHeld = useRef(false);

  // Seed the boot theme before the iframe loads (same-origin: shared storage).
  if (typeof window !== "undefined") {
    try { window.localStorage.setItem("ot-theme", theme); } catch { /* ignore */ }
  }

  const params = new URLSearchParams({ embed: "1", view });
  if (repo) params.set("repo", repo);
  if (dataset) params.set("dataset", dataset);
  if (child) params.set("child", child);
  if (tab) params.set("tab", tab);
  if (trace) params.set("trace", trace);
  if (pr) params.set("pr", pr);
  const src = `/hub-preview/index.html?${params.toString()}`;

  // Measure width → scale factor.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth;
      if (w > 0) setScale(w / canvasW);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [canvasW]);

  // Windowed mount with hysteresis: mount when within ~600px of the viewport,
  // and only UNMOUNT once it's >1600px away. The gap means normal up/down
  // reading never re-boots a frame; only fast scrolls far off reclaim it,
  // capping how many live iframes exist at once.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const ioMount = new IntersectionObserver(
      (entries) => { if (entries.some((e) => e.isIntersecting)) setMounted(true); },
      { rootMargin: "600px 0px 600px 0px" },
    );
    const ioUnmount = new IntersectionObserver(
      (entries) => {
        if (entries.every((e) => !e.isIntersecting)) {
          setMounted(false);
          setLoaded(false);
        }
      },
      { rootMargin: "1600px 0px 1600px 0px" },
    );
    ioMount.observe(el);
    ioUnmount.observe(el);
    return () => { ioMount.disconnect(); ioUnmount.disconnect(); };
  }, []);

  // Acquire a boot slot before actually rendering the iframe; release it on
  // load (or if windowed out before it finishes booting).
  useEffect(() => {
    if (!mounted) { setHasSlot(false); return; }
    let cancelled = false;
    acquireBoot().then(() => {
      if (cancelled) { releaseBoot(); return; }
      slotHeld.current = true;
      setHasSlot(true);
    });
    return () => {
      cancelled = true;
      if (slotHeld.current) { releaseBoot(); slotHeld.current = false; }
      setHasSlot(false);
    };
  }, [mounted]);

  // Follow the site theme top-down (one-directional; embed mode in the Hub app
  // intentionally does NOT push theme back up to the parent).
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

  const renderedWidth = scale * canvasW;
  const tooNarrow = scale > 0 && renderedWidth < MIN_WIDTH;
  const viewportHeight = scale > 0 ? height * scale : Math.min(height, 520);

  return (
    <div
      className="hub-feature-frame"
      ref={viewportRef}
      style={{ height: viewportHeight }}
      role="img"
      aria-label={`${label} — interactive Hub preview`}
    >
      {tooNarrow ? (
        <div className="hub-feature-narrow">
          <span className="hff-dot" aria-hidden="true" />
          interactive preview — best viewed on desktop
        </div>
      ) : (
        <>
          {/* Skeleton: the resting + loading state while a frame is parked. */}
          <div className={`hub-feature-skeleton${loaded ? " hidden" : ""}`} aria-hidden="true">
            <span className="hff-shimmer" />
          </div>
          {mounted && hasSlot && scale > 0 && (
            <iframe
              ref={iframeRef}
              className={`hub-feature-iframe${loaded ? " loaded" : ""}`}
              src={src}
              title={`${label} — Hub preview`}
              loading="lazy"
              tabIndex={-1}
              onLoad={() => {
                setLoaded(true);
                applyThemeToIframe(iframeRef.current, theme);
                if (slotHeld.current) { releaseBoot(); slotHeld.current = false; }
              }}
              style={{
                width: canvasW,
                height,
                transform: `scale(${scale})`,
                transformOrigin: "top left",
              }}
            />
          )}
        </>
      )}
    </div>
  );
}
