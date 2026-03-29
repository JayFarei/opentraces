"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { DOC_NAV } from "@/lib/doc-nav";

export default function DocsSidebar() {
  const pathname = usePathname();

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

  return (
    <aside className="docs-sidebar">
      {groups.map((group, gi) => (
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
              >
                {entry.title}
              </Link>
            );
          })}
        </div>
      ))}
    </aside>
  );
}
