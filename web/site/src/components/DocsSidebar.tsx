"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { DOC_NAV } from "@/lib/doc-nav";

export default function DocsSidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  // Group entries
  const groups: { label: string | null; items: typeof DOC_NAV }[] = [];

  for (const entry of DOC_NAV) {
    const group = entry.group || null;
    const lastGroup = groups.length > 0 ? groups[groups.length - 1] : null;

    if (!lastGroup || lastGroup.label !== group) {
      groups.push({ label: group, items: [entry] });
    } else {
      lastGroup.items.push(entry);
    }
  }

  const sidebarContent = groups.map((group, gi) => (
    <div key={gi} style={{ marginBottom: 16 }}>
      {group.label && (
        <div className="docs-sidebar-group">{group.label}</div>
      )}
      {group.items.map((entry) => {
        const href = `/docs${entry.slug ? `/${entry.slug}` : ""}`;
        const isActive = pathname === href || (entry.slug === "" && pathname === "/docs");
        return (
          <Link
            key={entry.slug}
            href={href}
            className={`docs-sidebar-link${isActive ? " active" : ""}`}
            onClick={() => setOpen(false)}
          >
            {entry.title}
          </Link>
        );
      })}
    </div>
  ));

  return (
    <>
      <button
        className="docs-sidebar-toggle"
        onClick={() => setOpen(!open)}
        aria-label="Toggle docs navigation"
        aria-expanded={open}
      >
        {open ? "✕ close" : "≡ menu"}
      </button>
      <aside className={`docs-sidebar${open ? " docs-sidebar-open" : ""}`}>
        {sidebarContent}
      </aside>
    </>
  );
}
