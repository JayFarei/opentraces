#!/usr/bin/env node
// Deterministic pre-deploy SEO gate (Phase 0, no credentials). Checks the BUILT
// site: per priority page JSON-LD validity + parity with the visible
// title/headings, no misframing or wrong load-bearing facts in curated
// structured data, canonical self-consistency, a distinct (non-generic) title,
// indexability (meta robots), robots Allow + Content-Signal values, and that
// the sitemap source isn't build-clock-stamped with differentiated dates.
// Exits non-zero on any failure so CI / a pre-deploy hook can block.
//   node scripts/seo/seo-check.mjs [--dist .next] [--json] [--write]
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import {
  SITE_DIR, SITE_ORIGIN, PRIORITY_PAGES, DEEP_PAGES, htmlFileForPath,
  appendRecord, runDateBucket, currentDeploySha,
} from "./lib.mjs";

const args = process.argv.slice(2);
const distDir = (() => {
  const i = args.indexOf("--dist");
  return i >= 0 ? args[i + 1] : process.env.NEXT_DIST_DIR || ".next";
})();
const asJson = args.includes("--json");
const write = args.includes("--write");

const checks = [];
const failures = [];
function record(name, ok, detail = "") {
  checks.push({ name, ok, detail });
  if (!ok) failures.push(`${name}${detail ? ": " + detail : ""}`);
}

const GENERIC_HOME_TITLE = "open traces - The Commons for Agent Traces";
// Misframings that must never appear in CURATED structured data (the product is
// explicitly local/open, not a centralized SaaS — see the mission note).
const BANNED_PHRASES = ["centralized store", "centralized saas", "centralized database", "proprietary", "closed-source", "closed source"];
// Curated node types we hand-author (vs auto-extracted TechArticle doc prose).
const CURATED_TYPES = new Set(["Organization", "WebSite", "SoftwareApplication", "CollectionPage"]);

function extractAll(html, re) {
  const out = [];
  let m;
  while ((m = re.exec(html))) out.push(m[1]);
  return out;
}

function checkPage(path, { allowGenericTitle = false } = {}) {
  const file = htmlFileForPath(distDir, path);
  if (!existsSync(file)) {
    record(`page:${path} prerendered`, false, `missing ${file} (run next build first?)`);
    return;
  }
  const html = readFileSync(file, "utf8");

  const title = (html.match(/<title>([^<]*)<\/title>/) || [])[1] || "";
  record(`page:${path} title`, !!title.trim(), title ? `"${title}"` : "empty");
  if (!allowGenericTitle) record(`page:${path} non-generic title`, title.trim() !== GENERIC_HOME_TITLE, title);

  const desc = (html.match(/<meta name="description" content="([^"]*)"/) || [])[1] || "";
  record(`page:${path} description`, !!desc.trim());

  const canonical = (html.match(/<link rel="canonical" href="([^"]*)"/) || [])[1] || "";
  const expected = path === "/" ? SITE_ORIGIN : SITE_ORIGIN + path;
  // Tolerate the home trailing-slash variant (canonical and sitemap loc differ).
  const canonOk = canonical === expected || canonical === expected.replace(/\/$/, "") || canonical === expected + "/";
  record(`page:${path} canonical`, canonOk, `got "${canonical}" want "${expected}"`);

  // Indexability: only the actual meta robots directive, not body/JS text.
  const metaRobots = (html.match(/<meta[^>]+name=["']robots["'][^>]*content=["']([^"']*)["']/i) || [])[1] || "";
  record(`page:${path} indexable`, !/noindex/i.test(metaRobots), metaRobots);

  const scripts = extractAll(html, /<script type="application\/ld\+json">([\s\S]*?)<\/script>/g);
  record(`page:${path} has structured data`, scripts.length >= 1, `${scripts.length} script(s)`);
  const nodes = [];
  let parseOk = scripts.length > 0;
  for (const s of scripts) {
    try {
      const d = JSON.parse(s);
      nodes.push(...(d["@graph"] || [d]));
    } catch (e) {
      parseOk = false;
      record(`page:${path} json-ld parses`, false, String(e.message));
    }
  }
  if (scripts.length) record(`page:${path} json-ld parses`, parseOk);

  // Visible-text haystack (alphanumeric-normalized so the brand "opentraces"
  // matches the styled visible "open traces"). Heading capture spans nested
  // tags (span-wrapped section labels) then strips markup.
  const headings = extractAll(html, /<h[12][^>]*>([\s\S]*?)<\/h[12]>/g).map((s) => s.replace(/<[^>]+>/g, " ")).join(" ");
  const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "");
  const haystack = norm(title) + " " + norm(headings);

  for (const n of nodes) {
    const url = n.url || n["@id"] || "";
    if (url) record(`page:${path} json-ld origin`, String(url).startsWith(SITE_ORIGIN), String(url));

    // Light gross-drift parity: name/headline first significant word is visible.
    const label = n.headline || n.name || "";
    if (label) {
      const word = String(label).toLowerCase().split(/\s+/).find((w) => w.length >= 4);
      if (word) record(`page:${path} json-ld parity`, haystack.includes(norm(word)), `"${label}"`);
    }

    // Misframing guard on CURATED nodes (skip auto-extracted doc prose).
    if (CURATED_TYPES.has(n["@type"])) {
      for (const f of ["name", "description"]) {
        const v = String(n[f] || "").toLowerCase();
        if (!v) continue;
        const bad = BANNED_PHRASES.find((b) => v.includes(b));
        record(`page:${path} no-misframing (${f})`, !bad, bad ? `banned "${bad}"` : "");
      }
    }

    // Load-bearing facts on the SoftwareApplication node.
    if (n["@type"] === "SoftwareApplication") {
      record(`page:${path} software license MIT`, /opensource\.org\/license\/mit/i.test(String(n.license || "")), String(n.license || ""));
      record(`page:${path} software language Python`, String(n.programmingLanguage || "") === "Python", String(n.programmingLanguage || ""));
      record(`page:${path} software free`, n.isAccessibleForFree === true, String(n.isAccessibleForFree));
    }
  }
}

checkPage("/", { allowGenericTitle: true });
for (const p of PRIORITY_PAGES.filter((p) => p !== "/")) checkPage(p);
for (const p of DEEP_PAGES) checkPage(p);

// robots.txt invariants: a real (non-comment) Allow: / , no blanket Disallow,
// and the Content-Signal VALUES (not just the header's presence).
const robots = (() => {
  const f = join(SITE_DIR, "public", "robots.txt");
  return existsSync(f) ? readFileSync(f, "utf8") : "";
})();
const robotLines = robots.split("\n").map((l) => l.replace(/#.*$/, "").trim());
record("robots allow /", robotLines.some((l) => /^allow:\s*\/\s*$/i.test(l)));
record("robots no blanket disallow", !robotLines.some((l) => /^disallow:\s*\/\s*$/i.test(l)));
const cs = (robots.match(/content-signal:\s*([^\n]*)/i) || [])[1] || "";
record("robots Content-Signal = yes", /search=yes/i.test(cs) && /ai-train=yes/i.test(cs) && /ai-input=yes/i.test(cs), cs);

// sitemap freshness smoke test (NOT a content-hash honesty check — see
// SEO-AEO-LOOP.md "Phase 0 status"): source must not build-clock-stamp, and the
// committed dates must be differentiated.
const sitemapSrc = (() => {
  const f = join(SITE_DIR, "src", "app", "sitemap.ts");
  return existsSync(f) ? readFileSync(f, "utf8") : "";
})();
record("sitemap not build-clock", !/lastModified:\s*now\b/.test(sitemapSrc) && !/lastModified:\s*new Date\(\)/.test(sitemapSrc));
const lastmod = (() => {
  const f = join(SITE_DIR, "src", "lib", "sitemap-lastmod.json");
  return existsSync(f) ? JSON.parse(readFileSync(f, "utf8")) : {};
})();
const distinct = new Set(Object.values(lastmod).map((d) => String(d).slice(0, 10)));
record("sitemap dates present", Object.keys(lastmod).length > 0, `${Object.keys(lastmod).length} urls`);
record("sitemap dates differentiated", distinct.size > 1, `${distinct.size} distinct dates`);

const sitemapXml = (() => {
  for (const cand of ["sitemap.xml.body", "sitemap.xml"]) {
    const f = join(SITE_DIR, distDir, "server", "app", cand);
    if (existsSync(f)) return readFileSync(f, "utf8");
  }
  return "";
})();
if (sitemapXml) {
  for (const p of PRIORITY_PAGES) record(`sitemap has ${p}`, sitemapXml.includes(`<loc>${SITE_ORIGIN + p}</loc>`));
} else {
  record("sitemap.xml built", false, "no sitemap.xml in build output");
}

const passed = checks.filter((c) => c.ok).length;
if (write) {
  appendRecord("standings.jsonl", {
    ts: new Date().toISOString(),
    run_date_bucket: runDateBucket(),
    deploy_sha: currentDeploySha(),
    surface: "seo_check",
    status: failures.length ? "error" : "complete",
    payload: { passed, failed: failures.length, failures, checks },
  });
}

if (asJson) {
  console.log(JSON.stringify({ passed, failed: failures.length, failures, checks }, null, 2));
} else {
  for (const c of checks) console.log(`${c.ok ? "✓" : "✗"} ${c.name}${c.detail ? "  — " + c.detail : ""}`);
  console.log(`\n${passed}/${checks.length} checks passed, ${failures.length} failed`);
}
process.exit(failures.length ? 1 : 0);
