"use client";

import { useEffect, useState } from "react";

const GH_URL = "https://github.com/jayfarei/opentraces";

export default function StarCallout() {
  const [pos, setPos] = useState<{ x: number; bottom: number } | null>(null);
  const [visible, setVisible] = useState(false);
  const [fading, setFading] = useState(false);

  useEffect(() => {
    if (localStorage.getItem("star-callout-dismissed")) return;

    function measure() {
      const el = document.querySelector(
        '.hero-metric-cell[data-metric-type="star"]'
      ) as HTMLElement | null;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setPos({ x: r.left + r.width / 2, bottom: r.bottom });
    }

    const showTimer = setTimeout(() => {
      measure();
      setVisible(true);
      window.addEventListener("scroll", measure, { passive: true });
      window.addEventListener("resize", measure, { passive: true });
    }, 600);

    const fadeTimer = setTimeout(() => setFading(true), 12000);
    const hideTimer = setTimeout(() => {
      setVisible(false);
      setFading(false);
    }, 13000);

    return () => {
      clearTimeout(showTimer);
      clearTimeout(fadeTimer);
      clearTimeout(hideTimer);
      window.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
  }, []);

  if (!visible || !pos) return null;

  return (
    <a
      href={GH_URL}
      target="_blank"
      rel="noopener noreferrer"
      className={`star-callout${fading ? " star-callout-fading" : ""}`}
      style={{ left: pos.x, top: pos.bottom }}
      onClick={() => localStorage.setItem("star-callout-dismissed", "1")}
    >
      <span className="star-callout-line" aria-hidden="true" />
      <span className="star-callout-text">
        give us a star on{" "}
        <span className="star-callout-link">github</span>!
      </span>
    </a>
  );
}
