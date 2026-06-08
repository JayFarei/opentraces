"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const GH_URL = "https://github.com/JayFarei/opentraces";

// Session-wide star cache so the nav badge resolves once and then renders
// instantly (and identically) on every page — the bar never changes size as
// you navigate. Pages that fetch the count server-side seed this via the prop;
// the rest read it from /api/stars.
let starCache: string | null = null;
let starPromise: Promise<string> | null = null;
function loadStars(): Promise<string> {
  if (starCache != null) return Promise.resolve(starCache);
  const promise =
    starPromise ??
    (starPromise = fetch("/api/stars")
      .then((r) => r.json())
      .then((d): string => (typeof d?.stars === "string" ? d.stars : "—"))
      .then((v) => {
        starCache = v;
        return v;
      })
      .catch((): string => {
        starCache = "—";
        return "—";
      }));
  return promise;
}

function resolveTheme(): "dark" | "light" {
  const stored = localStorage.getItem("theme") as "dark" | "light" | null;
  if (stored) return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(t: "dark" | "light") {
  const el = document.documentElement;
  el.setAttribute("data-theme", t);
  el.classList.remove("theme-dark", "theme-light");
  el.classList.add(t === "dark" ? "theme-dark" : "theme-light");
  el.style.colorScheme = t;
}

function ExternalArrow() {
  return (
    <svg
      width="9"
      height="9"
      viewBox="0 0 9 9"
      fill="none"
      aria-hidden="true"
      style={{ marginLeft: "3px", verticalAlign: "middle", opacity: 0.55, flexShrink: 0 }}
    >
      <path
        d="M1 8L8 1M8 1H3M8 1V6"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function StarIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true" style={{ flexShrink: 0 }}>
      <path
        d="m8 2 1.6 3.3 3.6.5-2.6 2.5.6 3.6L8 10.1 4.8 11.9l.6-3.6L2.8 5.8l3.6-.5Z"
        fill="currentColor"
        stroke="currentColor"
        strokeWidth="0.5"
        strokeLinejoin="miter"
      />
    </svg>
  );
}

export default function Nav({ stars: initialStars }: { stars?: string }) {
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    typeof window === "undefined" ? "light" : resolveTheme(),
  );
  const [menuOpen, setMenuOpen] = useState(false);
  // The cache is read client-only so the first (hydration) render matches the
  // server output (prop, or "—") and never mismatches; SPA navigations then pick
  // up the cached value immediately.
  const [stars, setStars] = useState<string | undefined>(
    initialStars ?? (typeof window !== "undefined" ? starCache ?? undefined : undefined),
  );

  useEffect(() => {
    if (initialStars) {
      if (starCache == null) starCache = initialStars; // seed cache for other pages
      return;
    }
    if (stars) return;
    let alive = true;
    loadStars().then((s) => { if (alive) setStars(s); });
    return () => { alive = false; };
  }, [initialStars, stars]);
  useEffect(() => {
    applyTheme(theme);

    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    function onSystemChange(e: MediaQueryListEvent) {
      if (!localStorage.getItem("theme")) {
        const next = e.matches ? "dark" : "light";
        setTheme(next);
        applyTheme(next);
      }
    }
    mq.addEventListener("change", onSystemChange);
    return () => mq.removeEventListener("change", onSystemChange);
  }, [theme]);

  function toggle() {
    document.body.style.transition = "background 0.15s, color 0.15s";
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
    localStorage.setItem("theme", next);
  }

  return (
    <nav className="nav">
      <Link href="/" className="nav-logo">
        <span className="brand-open">open</span><span className="brand-traces">traces</span>
      </Link>
      <button
        className="nav-hamburger"
        onClick={() => setMenuOpen(!menuOpen)}
        aria-label="Toggle menu"
        aria-expanded={menuOpen}
      >
        {menuOpen ? "✕" : "≡"}
      </button>
      <div className={`nav-links${menuOpen ? " nav-links-open" : ""}`}>
        <Link href="/schema" className="nav-link" onClick={() => setMenuOpen(false)}>schema</Link>
        <Link href="/explorer" className="nav-link" onClick={() => setMenuOpen(false)}>explorer</Link>
        <Link href="/hub" className="nav-link" onClick={() => setMenuOpen(false)}>
          hub<span className="nav-new-pill">new</span>
        </Link>
        <Link href="/docs" className="nav-link" onClick={() => setMenuOpen(false)}>docs</Link>
        <a href="/llms.txt" className="nav-link" target="_blank" rel="noopener noreferrer" onClick={() => setMenuOpen(false)}>/llms.txt</a>
        <a
          href={GH_URL}
          className="nav-link nav-github-link"
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => setMenuOpen(false)}
          data-star-nav
        >
          github<span className="nav-star-badge">&thinsp;[<StarIcon />{stars ?? "—"}]</span><ExternalArrow />
        </a>
        <span className="nav-divider" style={{ color: "var(--border)" }}>|</span>
        <button
          className="nav-theme-btn"
          onClick={toggle}
          aria-label="Toggle theme"
          suppressHydrationWarning
        >
          {theme === "dark" ? "light" : "dark"}
        </button>
      </div>
    </nav>
  );
}
