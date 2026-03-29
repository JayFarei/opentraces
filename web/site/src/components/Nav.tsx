"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

function resolveTheme(): "dark" | "light" {
  if (typeof window === "undefined") return "light";
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
  const [theme, setTheme] = useState<"dark" | "light">(() => resolveTheme());

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    function onSystemChange(e: MediaQueryListEvent) {
      if (!localStorage.getItem("theme")) {
        const next = e.matches ? "dark" : "light";
        setTheme(next);
      }
    }
    mq.addEventListener("change", onSystemChange);
    return () => mq.removeEventListener("change", onSystemChange);
  }, []);

  useEffect(() => {
    applyTheme(theme);
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
      <div className="nav-links">
        <Link href="/schema" className="nav-link">schema</Link>
        <Link href="/explorer" className="nav-link">explorer</Link>
        <Link href="/docs" className="nav-link">docs</Link>
        <a href="/llms.txt" className="nav-link">/llms.txt</a>
        <a href="https://github.com/jayfarei/opentraces" className="nav-link" target="_blank" rel="noopener noreferrer">github</a>
        <span style={{ color: "var(--border)" }}>|</span>
        <button className="nav-theme-btn" onClick={toggle} aria-label="Toggle theme">
          {theme === "dark" ? "light" : "dark"}
        </button>
      </div>
    </nav>
  );
}
