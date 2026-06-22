import type { MetadataRoute } from "next";
import { DOC_NAV } from "@/lib/doc-nav";
import lastmodMap from "@/lib/sitemap-lastmod.json";

const SITE = "https://opentraces.ai";

// Per-URL last-modified dates derived from git history (scripts/gen-sitemap-dates.mjs),
// so <lastmod> reflects a real content change instead of the build clock. Omit
// lastModified when we have no honest date for a URL (an inaccurate lastmod is
// worse than none — search engines discount it).
const LASTMOD = lastmodMap as Record<string, string>;

export default function sitemap(): MetadataRoute.Sitemap {
  const staticRoutes = [
    { path: "/", changeFrequency: "weekly" as const, priority: 1.0 },
    { path: "/blog/introducing-opentraces-0-4", changeFrequency: "monthly" as const, priority: 0.8 },
    { path: "/explorer", changeFrequency: "daily" as const, priority: 0.8 },
    { path: "/schema/latest", changeFrequency: "monthly" as const, priority: 0.7 },
  ];
  const docRoutes = DOC_NAV.map((d) => ({
    path: `/docs${d.slug ? "/" + d.slug : ""}`,
    changeFrequency: "weekly" as const,
    priority: d.slug ? 0.6 : 0.7,
  }));

  return [...staticRoutes, ...docRoutes].map((r) => {
    const entry: MetadataRoute.Sitemap[number] = {
      url: `${SITE}${r.path}`,
      changeFrequency: r.changeFrequency,
      priority: r.priority,
    };
    const lastModified = LASTMOD[r.path];
    if (lastModified) entry.lastModified = lastModified;
    return entry;
  });
}
