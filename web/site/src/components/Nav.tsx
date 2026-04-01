"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

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

export default function Nav() {
  const [theme, setTheme] = useState<"dark" | "light" | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const resolved = resolveTheme();
    setTheme(resolved);
    applyTheme(resolved);

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
  }, []);

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
        <a href="https://github.com/jayfarei/opentraces" className="nav-link" target="_blank" rel="noopener noreferrer" onClick={() => setMenuOpen(false)}>github</a>
        <span className="nav-divider" style={{ color: "var(--border)" }}>|</span>
        <button className="nav-theme-btn" onClick={toggle} aria-label="Toggle theme">
          {theme === null ? null : theme === "dark" ? "light" : "dark"}
        </button>
      </div>
    </nav>
  );
}
