"use client";

import { useEffect, useState } from "react";

interface TOCEntry {
  id: string;
  text: string;
  level: number;
}

export default function DocsTOC() {
  const [headings, setHeadings] = useState<TOCEntry[]>([]);
  const [activeId, setActiveId] = useState<string>("");

  useEffect(() => {
    const content = document.querySelector(".docs-content");
    if (!content) return;

    const els = content.querySelectorAll("h2, h3");
    const entries: TOCEntry[] = [];

    els.forEach((el) => {
      const text = el.textContent || "";
      const id = text
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, "")
        .replace(/\s+/g, "-");
      el.id = id;
      entries.push({
        id,
        text,
        level: el.tagName === "H2" ? 2 : 3,
      });
    });

    setHeadings(entries);
  }, []);

  useEffect(() => {
    if (headings.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveId(entry.target.id);
            break;
          }
        }
      },
      { rootMargin: "-80px 0px -60% 0px", threshold: 0 }
    );

    for (const h of headings) {
      const el = document.getElementById(h.id);
      if (el) observer.observe(el);
    }

    return () => observer.disconnect();
  }, [headings]);

  if (headings.length === 0) return null;

  return (
    <aside className="docs-toc">
      <div className="docs-toc-label">On this page</div>
      {headings.map((h) => (
        <a
          key={h.id}
          href={`#${h.id}`}
          className={`docs-toc-link${h.level === 3 ? " nested" : ""}${activeId === h.id ? " active" : ""}`}
        >
          {h.text}
        </a>
      ))}
    </aside>
  );
}
