"use client";

import { useEffect, useState } from "react";

function getSystemTheme(): "dark" | "light" {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light" | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem("theme") as "dark" | "light" | null;
    const initial = stored || getSystemTheme();
    setTheme(initial);
    applyTheme(initial);

    // Listen for system preference changes (only if user hasn't manually set)
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

  function applyTheme(t: "dark" | "light") {
    const el = document.documentElement;
    el.setAttribute("data-theme", t);
    el.classList.remove("theme-dark", "theme-light");
    el.classList.add(t === "dark" ? "theme-dark" : "theme-light");
  }

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
    localStorage.setItem("theme", next);
  }

  if (!theme) return null;

  return (
    <button className="theme-toggle" onClick={toggle} aria-label="Toggle theme">
      [{theme === "dark" ? "light" : "dark"}]
    </button>
  );
}
