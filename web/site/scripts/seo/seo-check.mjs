#!/usr/bin/env node
// Deterministic SEO check (Phase 0, no credentials). Two modes:
//   • BUILD (default): reads <dist>/server/app/*.html + source files — the
//     pre-deploy GATE wired into .github/workflows/seo-checks.yml.
//   • LIVE  (--url <base>): fetches the deployed site + its robots/sitemap — the
//     scheduled MONITOR wired into .github/workflows/seo-monitor.yml.
// Same checks either way: per priority page title/description/canonical/
// indexable, JSON-LD validity + parity + no-misframing on curated nodes +
// SoftwareApplication facts, robots Allow/no-blanket-Disallow/Content-Signal,
// sitemap presence + date differentiation. Exits non-zero on any failure.
//   node scripts/seo/seo-check.mjs [--dist .next] [--url https://www.opentraces.ai] [--json] [--write] [--force]
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import {
  SITE_DIR, SITE_ORIGIN, PRIORITY_PAGES, DEEP_PAGES, htmlFileForPath,
  appendRecord, readRecords, recordExists, runDateBucket, currentDeploySha,
} from "./lib.mjs";

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : fallback;
};
const urlBase = (opt("--url", null) || "").replace(/\/$/, "") || null;
const mode = urlBase ? "url" : "build";
const distDir = opt("--dist", process.env.NEXT_DIST_DIR || ".next");
const asJson = args.includes("--json");
const write = args.includes("--write");
const force = args.includes("--force");

const checks = [];
const failures = [];
function record(name, ok, detail = "") {
  checks.push({ name, ok, detail });
  if (!ok) failures.push(`${name}${detail ? ": " + detail : ""}`);
}

const GENERIC_HOME_TITLE = "open traces - The Commons for Agent Traces";
const BANNED_PHRASES = ["centralized store", "centralized saas", "centralized database", "proprietary", "closed-source", "closed source"];
const CURATED_TYPES = new Set(["Organization", "WebSite", "SoftwareApplication", "CollectionPage"]);

function extractAll(html, re) {
  const out = [];
  let m;
  while ((m = re.exec(html))) out.push(m[1]);
  return out;
}

async function fetchText(url) {
  const res = await fetch(url, { redirect: "follow" });
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return res.text();
}

// Acquire a priority page's HTML (live fetch or prerendered build file).
async function getPageHtml(path) {
  if (mode === "url") {
    const url = urlBase + (path === "/" ? "/" : path);
    try {
      return { html: await fetchText(url) };
    } catch (e) {
      return { error: String(e.message) };
    }
  }
  const file = htmlFileForPath(distDir, path);
  if (!existsSync(file)) return { error: `missing ${file} (run next build first?)` };
  return { html: readFileSync(file, "utf8") };
}

async function checkPage(path, { allowGenericTitle = false } = {}) {
  const { html, error } = await getPageHtml(path);
  if (error) {
    record(`page:${path} reachable`, false, error);
    return;
  }

  const title = (html.match(/<title>([^<]*)<\/title>/) || [])[1] || "";
  record(`page:${path} title`, !!title.trim(), title ? `"${title}"` : "empty");
  if (!allowGenericTitle) record(`page:${path} non-generic title`, title.trim() !== GENERIC_HOME_TITLE, title);

  const desc = (html.match(/<meta name="description" content="([^"]*)"/) || [])[1] || "";
  record(`page:${path} description`, !!desc.trim());

  const canonical = (html.match(/<link rel="canonical" href="([^"]*)"/) || [])[1] || "";
  const expected = path === "/" ? SITE_ORIGIN : SITE_ORIGIN + path;
  const canonOk = canonical === expected || canonical === expected.replace(/\/$/, "") || canonical === expected + "/";
  record(`page:${path} canonical`, canonOk, `got "${canonical}" want "${expected}"`);

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

  const headings = extractAll(html, /<h[12][^>]*>([\s\S]*?)<\/h[12]>/g).map((s) => s.replace(/<[^>]+>/g, " ")).join(" ");
  const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "");
  const haystack = norm(title) + " " + norm(headings);

  for (const n of nodes) {
    const url = n.url || n["@id"] || "";
    if (url) record(`page:${path} json-ld origin`, String(url).startsWith(SITE_ORIGIN), String(url));

    const label = n.headline || n.name || "";
    if (label) {
      const word = String(label).toLowerCase().split(/\s+/).find((w) => w.length >= 4);
      if (word) record(`page:${path} json-ld parity`, haystack.includes(norm(word)), `"${label}"`);
    }

    if (CURATED_TYPES.has(n["@type"])) {
      for (const f of ["name", "description"]) {
        const v = String(n[f] || "").toLowerCase();
        if (!v) continue;
        const bad = BANNED_PHRASES.find((b) => v.includes(b));
        record(`page:${path} no-misframing (${f})`, !bad, bad ? `banned "${bad}"` : "");
      }
    }

    if (n["@type"] === "SoftwareApplication") {
      record(`page:${path} software license MIT`, /opensource\.org\/license\/mit/i.test(String(n.license || "")), String(n.license || ""));
      record(`page:${path} software language Python`, String(n.programmingLanguage || "") === "Python", String(n.programmingLanguage || ""));
      record(`page:${path} software free`, n.isAccessibleForFree === true, String(n.isAccessibleForFree));
    }
  }
}

// ---- run -------------------------------------------------------------------
await checkPage("/", { allowGenericTitle: true });
for (const p of PRIORITY_PAGES.filter((p) => p !== "/")) await checkPage(p);
for (const p of DEEP_PAGES) await checkPage(p);

// robots + sitemap: fetched live (url mode) or read from disk/build (build mode).
let robots = "";
let sitemapXml = "";
let sitemapSrc = null; // build mode only
let lastmodJson = null; // build mode only
if (mode === "url") {
  robots = await fetchText(urlBase + "/robots.txt").catch(() => "");
  sitemapXml = await fetchText(urlBase + "/sitemap.xml").catch(() => "");
} else {
  const rf = join(SITE_DIR, "public", "robots.txt");
  robots = existsSync(rf) ? readFileSync(rf, "utf8") : "";
  for (const cand of ["sitemap.xml.body", "sitemap.xml"]) {
    const f = join(SITE_DIR, distDir, "server", "app", cand);
    if (existsSync(f)) { sitemapXml = readFileSync(f, "utf8"); break; }
  }
  const sf = join(SITE_DIR, "src", "app", "sitemap.ts");
  sitemapSrc = existsSync(sf) ? readFileSync(sf, "utf8") : "";
  const lf = join(SITE_DIR, "src", "lib", "sitemap-lastmod.json");
  lastmodJson = existsSync(lf) ? JSON.parse(readFileSync(lf, "utf8")) : {};
}

const robotLines = robots.split("\n").map((l) => l.replace(/#.*$/, "").trim());
record("robots allow /", robotLines.some((l) => /^allow:\s*\/\s*$/i.test(l)));
record("robots no blanket disallow", !robotLines.some((l) => /^disallow:\s*\/\s*$/i.test(l)));
const cs = (robots.match(/content-signal:\s*([^\n]*)/i) || [])[1] || "";
record("robots Content-Signal = yes", /search=yes/i.test(cs) && /ai-train=yes/i.test(cs) && /ai-input=yes/i.test(cs), cs);

// sitemap freshness smoke test (NOT content-hash honesty — see Phase 0 status).
if (sitemapSrc !== null) {
  record("sitemap not build-clock", !/lastModified:\s*now\b/.test(sitemapSrc) && !/lastModified:\s*new Date\(\)/.test(sitemapSrc));
}
const distinct = lastmodJson
  ? new Set(Object.values(lastmodJson).map((d) => String(d).slice(0, 10)))
  : new Set([...sitemapXml.matchAll(/<lastmod>([^<]+)<\/lastmod>/g)].map((m) => m[1].slice(0, 10)));
record("sitemap dates differentiated", distinct.size > 1, `${distinct.size} distinct dates`);

if (sitemapXml) {
  for (const p of PRIORITY_PAGES) record(`sitemap has ${p}`, sitemapXml.includes(`<loc>${SITE_ORIGIN + p}</loc>`));
} else {
  record("sitemap reachable", false, mode === "url" ? "could not fetch sitemap.xml" : "no sitemap.xml in build output");
}

// ---- report + optional snapshot --------------------------------------------
const passed = checks.filter((c) => c.ok).length;
if (write) {
  const key = { run_date_bucket: runDateBucket(), deploy_sha: currentDeploySha(), surface: "seo_check" };
  if (!force && recordExists("standings.jsonl", key)) {
    console.error(`(idempotent) seo_check already recorded for ${key.run_date_bucket}/${key.deploy_sha} — use --force to override`);
  } else {
    appendRecord("standings.jsonl", {
      ts: new Date().toISOString(),
      ...key,
      mode,
      target: urlBase || `build:${distDir}`,
      status: failures.length ? "error" : "complete",
      payload: { passed, failed: failures.length, failures, checks },
    });
  }
}

if (asJson) {
  console.log(JSON.stringify({ mode, target: urlBase || `build:${distDir}`, passed, failed: failures.length, failures, checks }, null, 2));
} else {
  for (const c of checks) console.log(`${c.ok ? "✓" : "✗"} ${c.name}${c.detail ? "  — " + c.detail : ""}`);
  console.log(`\n[${mode}] ${passed}/${checks.length} checks passed, ${failures.length} failed`);
}
process.exit(failures.length ? 1 : 0);
