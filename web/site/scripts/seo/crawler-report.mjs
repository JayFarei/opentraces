#!/usr/bin/env node
// AI/search crawler-hit report from server/edge logs (Phase 0; the ~2-4 week
// LEADING indicator for AI citations — bots don't run JS, so client analytics
// can't see them). Reads logs from a file arg or stdin; accepts NDJSON or plain
// access-log text; extracts (path, userAgent, ip); matches the AI crawler set.
//
// With --verify, the LOAD-BEARING signal (coverage + total) counts ONLY hits
// whose source IP reverse-DNS + forward-confirms to the crawler's org — forged
// `User-Agent: GPTBot` from a random IP is excluded, so a spoofer can neither
// inflate presence nor mask a real zero-fetch regression. Claimed (unverified)
// counts are reported separately. Appends a `crawler_hits` standings record.
//
//   vercel logs <deploymentId> --json | node scripts/seo/crawler-report.mjs --verify
//   node scripts/seo/crawler-report.mjs ./access.ndjson --verify
// NOTE: a log SOURCE must be wired (Vercel Log Drain / `vercel logs`) for real
// data — see SEO-AEO-LOOP.md "Phase 0 status". Without --verify the counts are
// unverified and spoofable; treat them as claimed, not signal.
import { readFileSync } from "node:fs";
import { promises as dns } from "node:dns";
import { matchCrawler, appendRecord, runDateBucket, currentDeploySha, PRIORITY_PAGES } from "./lib.mjs";

const args = process.argv.slice(2);
const verify = args.includes("--verify");
const asJson = args.includes("--json");
const noWrite = args.includes("--no-write");
const fileArg = args.find((a) => !a.startsWith("--"));
const isFixture = !!fileArg && fileArg.includes("__fixtures__");

const flat = (v) => (Array.isArray(v) ? v[0] : v); // log fields can be arrays
const firstIp = (v) => String(flat(v) || "").split(",")[0].trim();
function cleanPath(u) {
  u = String(flat(u) || "");
  if (!u) return "";
  try {
    return new URL(u, "https://x").pathname;
  } catch {
    return u.split("?")[0];
  }
}

function parseLine(line) {
  line = line.trim();
  if (!line) return null;
  if (line.startsWith("{")) {
    try {
      const o = JSON.parse(line);
      const ua = flat(o.userAgent || o.ua || o["http.user_agent"] || o?.proxy?.userAgent || o?.request?.headers?.["user-agent"] || "");
      const path = o.path || o.url || o?.proxy?.path || o?.request?.url || "";
      const ip = o.clientIp || o.ip || o?.proxy?.clientIp || o?.request?.headers?.["x-forwarded-for"] || "";
      return { ua: String(ua || ""), path: cleanPath(path), ip: firstIp(ip), ts: o.timestamp || o.ts || o.time || "" };
    } catch {
      return null;
    }
  }
  // Plain text: scan ALL quoted tokens for a crawler UA (no end-of-line anchor,
  // so trailing log fields after the UA don't drop the hit).
  const ipM = line.match(/^(\d{1,3}(?:\.\d{1,3}){3}|[a-f0-9:]+)\b/i);
  const reqM = line.match(/"[A-Z]+\s+(\S+)\s+HTTP/);
  let ua = "";
  for (const m of line.matchAll(/"([^"]*)"/g)) {
    if (matchCrawler(m[1])) { ua = m[1]; break; }
  }
  return { ua, path: cleanPath(reqM ? reqM[1] : ""), ip: ipM ? ipM[1] : "", ts: "" };
}

function normIp(ip) {
  ip = String(ip).toLowerCase().trim();
  if (!ip.includes(":")) return ip; // IPv4
  const [head, tail = ""] = ip.split("::");
  const h = head ? head.split(":") : [];
  const t = tail ? tail.split(":") : [];
  const fill = 8 - h.length - t.length;
  return [...h, ...Array(Math.max(0, fill)).fill("0"), ...t].map((g) => (g || "0").padStart(4, "0")).join(":");
}

async function rdnsOk(ip, suffixes) {
  if (!ip) return false;
  try {
    const hosts = await dns.reverse(ip);
    if (!hosts.some((host) => suffixes.some((s) => host.endsWith(s)))) return false;
    const want = normIp(ip);
    for (const host of hosts) {
      try {
        const fwd = await dns.lookup(host, { all: true });
        if (fwd.some((a) => normIp(a.address) === want)) return true;
      } catch {
        /* try next PTR host */
      }
    }
    return false;
  } catch {
    return false;
  }
}

const hits = [];
for (const line of readFileSync(fileArg ?? 0, "utf8").split("\n")) {
  const rec = parseLine(line);
  if (!rec || !rec.ua) continue;
  const c = matchCrawler(rec.ua);
  if (c) hits.push({ ...rec, crawler: c });
}

const byCrawler = {};
const verifiedIp = new Map();
for (const h of hits) {
  const slot = (byCrawler[h.crawler.ua] ||= { org: h.crawler.org, claimed: 0, verified: 0, pathsClaimed: {}, pathsVerified: {} });
  slot.claimed++;
  slot.pathsClaimed[h.path] = (slot.pathsClaimed[h.path] || 0) + 1;
  let ok = false;
  if (verify && h.ip) {
    const key = h.crawler.ua + "|" + h.ip;
    if (!verifiedIp.has(key)) verifiedIp.set(key, await rdnsOk(h.ip, h.crawler.verifyHost));
    ok = verifiedIp.get(key);
  }
  if (ok) {
    slot.verified++;
    slot.pathsVerified[h.path] = (slot.pathsVerified[h.path] || 0) + 1;
  }
}

// The signal the loop ACTS on: verified-only when --verify, else claimed.
const pathsOf = (c) => (verify ? c.pathsVerified : c.pathsClaimed);
const watch = [...PRIORITY_PAGES, "/llms.txt"];
const coverage = {};
for (const p of watch) coverage[p] = Object.values(byCrawler).reduce((s, c) => s + (pathsOf(c)[p] || 0), 0);
const signalTotal = Object.values(byCrawler).reduce((s, c) => s + (verify ? c.verified : c.claimed), 0);

const record = {
  ts: new Date().toISOString(),
  run_date_bucket: runDateBucket(),
  deploy_sha: currentDeploySha(),
  surface: "crawler_hits",
  status: "complete",
  payload: {
    signal: verify ? "verified" : "claimed",
    total_signal: signalTotal,
    total_claimed: hits.length,
    distinct_crawlers: Object.keys(byCrawler).length,
    by_crawler: byCrawler,
    coverage,
  },
};
if (!noWrite && !isFixture) appendRecord("standings.jsonl", record);

if (asJson) {
  console.log(JSON.stringify(record, null, 2));
} else {
  console.log(`AI/search crawler hits — signal=${verify ? "verified" : "claimed (unverified, spoofable)"}: ${signalTotal} (claimed ${hits.length}), ${Object.keys(byCrawler).length} crawlers`);
  for (const [ua, c] of Object.entries(byCrawler)) {
    console.log(`  ${ua} [${c.org}]  claimed=${c.claimed}${verify ? `  verified=${c.verified}` : ""}`);
  }
  console.log(`Priority-page + llms.txt coverage (${verify ? "verified" : "claimed"}):`);
  for (const [p, n] of Object.entries(coverage)) console.log(`  ${p}: ${n}${n === 0 ? "  ⚠ zero fetches (precondition for citation)" : ""}`);
  if (isFixture) console.log("\n(fixture input — not writing to standings)");
  else if (!noWrite) console.log("\nappended crawler_hits record to seo-snapshots/standings.jsonl");
}
