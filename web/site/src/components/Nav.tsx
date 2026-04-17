"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const GH_URL = "https://github.com/JayFarei/opentraces";

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

export default function Nav({ stars }: { stars?: string }) {
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    typeof window === "undefined" ? "light" : resolveTheme(),
  );
  const [menuOpen, setMenuOpen] = useState(false);
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
          github{stars && <span className="nav-star-badge">&thinsp;[<StarIcon />{stars}]</span>}<ExternalArrow />
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
