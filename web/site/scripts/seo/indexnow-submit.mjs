#!/usr/bin/env node
// IndexNow submission (Phase 0). Reads the sitemap, extracts URLs, and submits
// them to IndexNow so participating engines (Bing, Yandex, …) recrawl on
// demand. DEFAULT IS DRY-RUN — prints the payload, sends nothing, and writes
// NOTHING (a dry run observes nothing about IndexNow, so it never touches the
// standings ledger). Pass --submit to actually POST; the key file
// public/<key>.txt must be live AND contain the configured key first.
//
//   node scripts/seo/indexnow-submit.mjs                 # dry run (default)
//   node scripts/seo/indexnow-submit.mjs --submit        # really submit
//   node scripts/seo/indexnow-submit.mjs --sitemap <url> # override sitemap
// (IndexNow keys are NOT secret — they're publicly hosted on the domain.)
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { SITE_DIR, appendRecord, runDateBucket, currentDeploySha } from "./lib.mjs";

const cfg = JSON.parse(readFileSync(join(SITE_DIR, "scripts/seo/indexnow.config.json"), "utf8"));
const args = process.argv.slice(2);
const submit = args.includes("--submit");
const noWrite = args.includes("--no-write");
const sitemapUrl = (() => {
  const i = args.indexOf("--sitemap");
  return i >= 0 ? args[i + 1] : `https://${cfg.host.replace(/^www\./, "")}/sitemap.xml`;
})();

async function getSitemapUrls() {
  const res = await fetch(sitemapUrl);
  if (!res.ok) throw new Error(`sitemap fetch ${res.status}`);
  return [...(await res.text()).matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
}

const urlList = await getSitemapUrls();
const payload = { host: cfg.host, key: cfg.key, keyLocation: cfg.keyLocation, urlList };

// A dry run observes nothing about IndexNow, so it never writes; --no-write also
// suppresses the record on a real submit attempt.
function writeRecord(result) {
  if (noWrite || !submit) return;
  appendRecord("standings.jsonl", {
    ts: new Date().toISOString(),
    run_date_bucket: runDateBucket(),
    deploy_sha: currentDeploySha(),
    surface: "indexnow",
    status: /^http_2/.test(result) ? "complete" : "error",
    payload: { submitted: true, url_count: urlList.length, result },
  });
}

console.log(`IndexNow ${submit ? "SUBMIT" : "DRY-RUN"} — ${urlList.length} urls from ${sitemapUrl}`);
console.log(`  endpoint:    ${cfg.endpoint}`);
console.log(`  keyLocation: ${cfg.keyLocation}`);
console.log(`  sample:      ${urlList.slice(0, 3).join("  ")}${urlList.length > 3 ? "  …" : ""}`);

if (!submit) {
  console.log("  (dry run — pass --submit to send; nothing was sent or written)");
} else {
  const keyRes = await fetch(cfg.keyLocation).catch(() => null);
  if (!keyRes || !keyRes.ok) {
    console.error(`✗ key file ${cfg.keyLocation} not reachable (HTTP ${keyRes?.status ?? "ERR"}). Deploy it first.`);
    writeRecord("key_unreachable");
    process.exit(2);
  }
  const body = (await keyRes.text()).trim();
  if (body !== cfg.key) {
    console.error(`✗ key file content mismatch (got "${body.slice(0, 16)}…", want "${cfg.key}"). Not submitting.`);
    writeRecord("key_mismatch");
    process.exit(2);
  }
  const res = await fetch(cfg.endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
  console.log(`  → ${res.status} ${res.statusText}`);
  writeRecord(`http_${res.status}`);
}
