#!/usr/bin/env node
// Google Search Console pull (Phase 1). The first CREDENTIALED monitor signal:
// per priority page index verdict + canonical drift (URL Inspection API) and
// clicks/impressions/CTR/position + top queries (Search Analytics API), appended
// as `gsc_index` and `gsc_search_analytics` standings records (dedupe-guarded).
//
// Auth is a service-account JWT (RS256) signed with Node's stdlib crypto — NO
// npm deps. It NO-OPS gracefully (exit 0) when no credentials are present, so it
// is safe to leave in the scheduled monitor before the secret is added.
//
//   GSC_SERVICE_ACCOUNT_JSON='<sa json or path>' GSC_SITE_URL='sc-domain:opentraces.ai' \
//     node scripts/seo/gsc-report.mjs --write
//   node scripts/seo/gsc-report.mjs --self-test    # offline: JWT + parse logic
// Setup: scripts/seo/GSC-SETUP.md.
import crypto from "node:crypto";
import { readFileSync, existsSync } from "node:fs";
import { SITE_ORIGIN, PRIORITY_PAGES, appendRecord, recordExists, runDateBucket, currentDeploySha } from "./lib.mjs";

const args = process.argv.slice(2);
const write = args.includes("--write");
const force = args.includes("--force");
const asJson = args.includes("--json");
const SCOPE = "https://www.googleapis.com/auth/webmasters.readonly";
const SITE_URL = process.env.GSC_SITE_URL || "sc-domain:opentraces.ai";

// ---- service-account auth (stdlib only) ------------------------------------
function b64url(input) {
  return Buffer.from(typeof input === "string" ? input : JSON.stringify(input)).toString("base64url");
}
function buildJwt(sa, scope) {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: "RS256", typ: "JWT" };
  const claims = { iss: sa.client_email, scope, aud: sa.token_uri || "https://oauth2.googleapis.com/token", iat: now, exp: now + 3600 };
  const unsigned = `${b64url(header)}.${b64url(claims)}`;
  const sig = crypto.createSign("RSA-SHA256").update(unsigned).sign(sa.private_key).toString("base64url");
  return `${unsigned}.${sig}`;
}
async function getAccessToken(sa, scope) {
  const tokenUri = sa.token_uri || "https://oauth2.googleapis.com/token";
  const res = await fetch(tokenUri, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion: buildJwt(sa, scope) }),
  });
  if (!res.ok) throw new Error(`token endpoint ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return (await res.json()).access_token;
}
function loadServiceAccount() {
  const raw = process.env.GSC_SERVICE_ACCOUNT_JSON || process.env.GOOGLE_APPLICATION_CREDENTIALS;
  if (!raw) return null;
  try {
    return JSON.parse(raw); // inline JSON (CI secret)
  } catch {
    if (existsSync(raw)) return JSON.parse(readFileSync(raw, "utf8")); // a path
    throw new Error("GSC_SERVICE_ACCOUNT_JSON is neither valid JSON nor a readable file path");
  }
}

// ---- API calls + parsers ----------------------------------------------------
async function inspectUrl(token, siteUrl, inspectionUrl) {
  const res = await fetch("https://searchconsole.googleapis.com/v1/urlInspection/index:inspect", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ inspectionUrl, siteUrl }),
  });
  if (!res.ok) throw new Error(`urlInspection ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return res.json();
}
function parseInspection(json) {
  const r = json?.inspectionResult?.indexStatusResult || {};
  const canonicalMatch = r.googleCanonical && r.userCanonical ? r.googleCanonical === r.userCanonical : null;
  return {
    verdict: r.verdict ?? null,
    coverageState: r.coverageState ?? null,
    robotsTxtState: r.robotsTxtState ?? null,
    indexingState: r.indexingState ?? null,
    pageFetchState: r.pageFetchState ?? null,
    lastCrawlTime: r.lastCrawlTime ?? null,
    googleCanonical: r.googleCanonical ?? null,
    userCanonical: r.userCanonical ?? null,
    canonicalMatch,
  };
}
async function searchAnalytics(token, siteUrl, body) {
  const res = await fetch(`https://www.googleapis.com/webmasters/v3/sites/${encodeURIComponent(siteUrl)}/searchAnalytics/query`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`searchAnalytics ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return res.json();
}
const round = (n, d = 2) => (typeof n === "number" ? Number(n.toFixed(d)) : n);
function parseAnalyticsRows(rows) {
  return (rows || []).map((r) => ({
    key: r.keys?.[0] ?? null,
    clicks: r.clicks ?? 0,
    impressions: r.impressions ?? 0,
    ctr: round(r.ctr, 4),
    position: round(r.position, 1),
  }));
}

// GSC data lags ~3 days; build trailing windows from there. GSC reporting days
// are Pacific (America/Los_Angeles), so bucket dates there (en-CA → YYYY-MM-DD)
// to align window edges to GSC's day boundaries rather than UTC's.
const isoDate = (ms) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Los_Angeles" }).format(new Date(ms));
const daysAgo = (n) => Date.now() - n * 86400000;

function writeRecord(surface, status, payload) {
  if (!write) return false;
  const key = { run_date_bucket: runDateBucket(), deploy_sha: currentDeploySha(), surface };
  if (!force && recordExists("standings.jsonl", key)) {
    console.error(`(idempotent) ${surface} already recorded for ${key.run_date_bucket}/${key.deploy_sha} — use --force`);
    return false;
  }
  appendRecord("standings.jsonl", { ts: new Date().toISOString(), ...key, status, payload });
  return true;
}

// ---- self test (offline: JWT roundtrip + parse logic) ----------------------
if (args.includes("--self-test")) {
  const { privateKey, publicKey } = crypto.generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding: { type: "spki", format: "pem" },
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
  });
  const sa = { client_email: "t@x.iam.gserviceaccount.com", private_key: privateKey, token_uri: "https://oauth2.googleapis.com/token" };
  const [h, c, s] = buildJwt(sa, SCOPE).split(".");
  const sigOk = crypto.createVerify("RSA-SHA256").update(`${h}.${c}`).verify(publicKey, Buffer.from(s, "base64url"));
  const claims = JSON.parse(Buffer.from(c, "base64url").toString());
  const insp = parseInspection({ inspectionResult: { indexStatusResult: { verdict: "PASS", coverageState: "Submitted and indexed", googleCanonical: "https://opentraces.ai/docs", userCanonical: "https://opentraces.ai/docs", lastCrawlTime: "2026-06-20T10:00:00Z" } } });
  const drift = parseInspection({ inspectionResult: { indexStatusResult: { googleCanonical: "https://opentraces.ai/x", userCanonical: "https://opentraces.ai/y" } } });
  const rows = parseAnalyticsRows([{ keys: ["https://opentraces.ai/docs"], clicks: 5, impressions: 100, ctr: 0.05, position: 8.234 }]);
  const t = [];
  t.push(["jwt signature verifies", sigOk === true]);
  t.push(["jwt iss/scope/exp", claims.iss === sa.client_email && claims.scope === SCOPE && claims.exp > claims.iat]);
  t.push(["jwt header alg RS256", JSON.parse(Buffer.from(h, "base64url").toString()).alg === "RS256"]);
  t.push(["inspection verdict parsed", insp.verdict === "PASS" && insp.canonicalMatch === true]);
  t.push(["canonical drift detected", drift.canonicalMatch === false]);
  t.push(["analytics row parsed", rows[0].clicks === 5 && rows[0].position === 8.2 && rows[0].ctr === 0.05]);
  let fail = 0;
  for (const [name, ok] of t) { console.log(`${ok ? "✓" : "✗"} ${name}`); if (!ok) fail++; }
  console.log(`\nself-test: ${t.length - fail}/${t.length} passed`);
  process.exit(fail ? 1 : 0);
}

// ---- main -------------------------------------------------------------------
const sa = loadServiceAccount();
if (!sa) {
  console.error("GSC not configured (set GSC_SERVICE_ACCOUNT_JSON) — skipping. See scripts/seo/GSC-SETUP.md");
  process.exit(0);
}

const token = await getAccessToken(sa, SCOPE);

// 1. URL Inspection per priority page.
const inspections = {};
for (const p of PRIORITY_PAGES) {
  const url = SITE_ORIGIN + (p === "/" ? "/" : p);
  try {
    inspections[url] = parseInspection(await inspectUrl(token, SITE_URL, url));
  } catch (e) {
    inspections[url] = { error: String(e.message) };
  }
}
// Derive status from outcomes — a run where every page errored (e.g. the 403
// "service account not granted on the property" blocker) must NOT read green.
const inspValues = Object.values(inspections);
const errored = inspValues.filter((r) => r.error).length;
writeRecord("gsc_index", inspValues.length && errored === inspValues.length ? "error" : "complete", {
  site: SITE_URL,
  pages: inspections,
  errored,
});

// 2. Search Analytics — trailing 7 & 28-day windows (3-day lag) + top queries.
const end = isoDate(daysAgo(3));
const windows = { "7d": isoDate(daysAgo(9)), "28d": isoDate(daysAgo(30)) };
const analytics = { site: SITE_URL, window_end: end, by_window: {} };
try {
  for (const [label, start] of Object.entries(windows)) {
    const perPage = parseAnalyticsRows((await searchAnalytics(token, SITE_URL, { startDate: start, endDate: end, dimensions: ["page"], rowLimit: 100 })).rows);
    analytics.by_window[label] = { start, end, per_page: perPage };
  }
  analytics.top_queries_28d = parseAnalyticsRows(
    (await searchAnalytics(token, SITE_URL, { startDate: windows["28d"], endDate: end, dimensions: ["query"], rowLimit: 25 })).rows,
  ).map(({ key, ...rest }) => ({ query: key, ...rest }));
  writeRecord("gsc_search_analytics", "complete", analytics);
} catch (e) {
  analytics.error = String(e.message);
  writeRecord("gsc_search_analytics", "error", analytics);
}

if (asJson) console.log(JSON.stringify({ inspections, analytics }, null, 2));
else {
  console.log(`GSC pull for ${SITE_URL} (analytics window ends ${end}):`);
  for (const [url, r] of Object.entries(inspections)) {
    console.log(`  ${url}: ${r.error ? "ERROR " + r.error : `${r.verdict ?? "?"} / ${r.coverageState ?? "?"}${r.canonicalMatch === false ? "  ⚠ canonical drift" : ""}`}`);
  }
  const pp28 = analytics.by_window?.["28d"]?.per_page || [];
  console.log(`  search analytics 28d: ${pp28.length} pages, ${(analytics.top_queries_28d || []).length} top queries`);
}
