"use client";

import { useEffect, useState } from "react";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light" | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem("theme") as "dark" | "light" | null;
    const initial = stored || "light";
    setTheme(initial);
    document.documentElement.setAttribute("data-theme", initial);
  }, []);

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
  }

  if (!theme) return null;

  return (
    <button className="theme-toggle" onClick={toggle} aria-label="Toggle theme">
      [{theme === "dark" ? "light" : "dark"}]
    </button>
  );
}
