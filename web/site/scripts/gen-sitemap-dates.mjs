// Generates src/lib/sitemap-lastmod.json: a { urlPath -> ISO date } map where
// each date is the git last-commit time of that page's source. This makes the
// sitemap's <lastmod> reflect a REAL content change instead of the build clock
// (the old `lastModified: new Date()` falsely marked every URL changed on every
// deploy). Run from web/site/. The JSON is committed and read at build time, so
// Vercel never needs git history. Re-run when content changes:
//   node scripts/gen-sitemap-dates.mjs
import { execSync } from "node:child_process";
import { readdirSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";

const SITE_DIR = process.cwd();
const DOCS_DIR = join(SITE_DIR, "docs", "docs");

function gitDate(file) {
  try {
    return (
      execSync(`git log -1 --format=%cI -- "${file}"`, {
        cwd: SITE_DIR,
        encoding: "utf8",
      }).trim() || null
    );
  } catch {
    return null;
  }
}

function walkMarkdown(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walkMarkdown(p));
    else if (name.endsWith(".md")) out.push(p);
  }
  return out;
}

const map = {};

// Docs markdown -> /docs[/slug]
for (const file of walkMarkdown(DOCS_DIR)) {
  const rel = relative(DOCS_DIR, file).replace(/\.md$/, "");
  const slug = rel === "index" ? "" : rel;
  const path = `/docs${slug ? "/" + slug : ""}`;
  const d = gitDate(file);
  if (d) map[path] = d;
}

// Static routes -> the primary source that defines each page's content
const staticSources = {
  "/": "src/app/page.tsx",
  "/explorer": "src/app/explorer/page.tsx",
  "/schema/latest": "src/lib/schema-versions.ts",
  "/blog/introducing-opentraces-0-4":
    "public/blog/introducing-opentraces-0-4/index.html",
};
for (const [path, src] of Object.entries(staticSources)) {
  const d = gitDate(src);
  if (d) map[path] = d;
}

const sorted = Object.fromEntries(
  Object.keys(map)
    .sort()
    .map((k) => [k, map[k]]),
);
writeFileSync(
  join(SITE_DIR, "src/lib/sitemap-lastmod.json"),
  JSON.stringify(sorted, null, 2) + "\n",
);
console.log(`wrote ${Object.keys(sorted).length} sitemap lastmod dates`);
