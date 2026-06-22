import type { MetadataRoute } from "next";
import { DOC_NAV } from "@/lib/doc-nav";

const SITE = "https://opentraces.ai";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
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

  return [...staticRoutes, ...docRoutes].map((r) => ({
    url: `${SITE}${r.path}`,
    lastModified: now,
    changeFrequency: r.changeFrequency,
    priority: r.priority,
  }));
}
