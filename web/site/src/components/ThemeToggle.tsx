"use client";

import { useEffect, useState } from "react";

function getSystemTheme(): "dark" | "light" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(t: "dark" | "light") {
  const el = document.documentElement;
  el.setAttribute("data-theme", t);
  el.classList.remove("theme-dark", "theme-light");
  el.classList.add(t === "dark" ? "theme-dark" : "theme-light");
}

function resolveTheme(): "dark" | "light" {
  const stored = localStorage.getItem("theme") as "dark" | "light" | null;
  return stored || getSystemTheme();
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    typeof window === "undefined" ? "light" : resolveTheme(),
  );

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
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
    localStorage.setItem("theme", next);
  }

  return (
    <button
      className="theme-toggle"
      onClick={toggle}
      aria-label="Toggle theme"
      suppressHydrationWarning
    >
      {theme === "dark" ? "light" : "dark"}
    </button>
  );
}
