"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

function getSystemTheme(): "dark" | "light" {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export default function Nav() {
  const [theme, setTheme] = useState<"dark" | "light" | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem("theme") as "dark" | "light" | null;
    const initial = stored || getSystemTheme();
    setTheme(initial);
    document.documentElement.setAttribute("data-theme", initial);

    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    function onSystemChange(e: MediaQueryListEvent) {
      if (!localStorage.getItem("theme")) {
        const next = e.matches ? "dark" : "light";
        setTheme(next);
        document.documentElement.setAttribute("data-theme", next);
      }
    }
    mq.addEventListener("change", onSystemChange);
    return () => mq.removeEventListener("change", onSystemChange);
  }, []);

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
  }

  return (
    <nav className="nav">
      <Link href="/" className="nav-logo">
        <span className="brand-open">open</span><span className="brand-traces">traces</span>
      </Link>
      <div className="nav-links">
        <Link href="/schema" className="nav-link">schema</Link>
        <Link href="/dashboard" className="nav-link">dashboard</Link>
        <Link href="/docs" className="nav-link">docs</Link>
        <a href="/llms.txt" className="nav-link">/llms.txt</a>
        <a href="https://github.com/opentraces" className="nav-link" target="_blank" rel="noopener noreferrer">github</a>
        {theme && (
          <>
            <span style={{ color: "var(--border)" }}>|</span>
            <button className="nav-theme-btn" onClick={toggle} aria-label="Toggle theme">
              {theme === "dark" ? "light" : "dark"}
            </button>
          </>
        )}
      </div>
    </nav>
  );
}
