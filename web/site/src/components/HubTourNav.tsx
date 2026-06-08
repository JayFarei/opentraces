"use client";

import { useEffect, useState } from "react";
import { HUB_FEATURE_GROUPS } from "@/lib/hub-features";

// Pinned table-of-contents for the /hub tour. Sticky on desktop; scroll-spy
// highlights the feature currently in view and clicking jumps to it.
export default function HubTourNav() {
  const allIds = HUB_FEATURE_GROUPS.flatMap((g) => g.features.map((f) => f.id));
  const [active, setActive] = useState(allIds[0]);

  useEffect(() => {
    const visible = new Map<string, number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = e.target.id.replace("feature-", "");
          if (e.isIntersecting) visible.set(id, e.boundingClientRect.top);
          else visible.delete(id);
        }
        let best: string | null = null;
        let bestTop = Infinity;
        visible.forEach((top, id) => {
          if (top < bestTop) { bestTop = top; best = id; }
        });
        if (best) setActive(best);
      },
      // Active zone = the band just below the sticky site nav.
      { rootMargin: "-92px 0px -55% 0px", threshold: 0 },
    );
    allIds.forEach((id) => {
      const el = document.getElementById(`feature-${id}`);
      if (el) io.observe(el);
    });
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const jump = (id: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    const el = document.getElementById(`feature-${id}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      setActive(id);
    }
  };

  return (
    <nav className="hub-tour-nav" aria-label="Feature tour contents">
      <div className="hub-tour-nav-title">on this page</div>
      {HUB_FEATURE_GROUPS.map((group) => (
        <div className="hub-tour-nav-group" key={group.id}>
          <div className="hub-tour-nav-glabel">{group.label}</div>
          <ul>
            {group.features.map((f) => (
              <li key={f.id}>
                <a
                  href={`#feature-${f.id}`}
                  onClick={jump(f.id)}
                  className={active === f.id ? "active" : undefined}
                  aria-current={active === f.id ? "true" : undefined}
                >
                  {f.heading}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
