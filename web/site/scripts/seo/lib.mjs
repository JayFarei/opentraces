// Shared helpers for the Phase 0 SEO/AEO monitoring scripts.
// Phase 0 is the free, no-credential, in-repo slice of SEO-AEO-LOOP.md:
// deterministic checks + an append-only standings store. No API keys here.
import { appendFileSync, readFileSync, existsSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { execSync } from "node:child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));
export const SITE_DIR = resolve(__dirname, "..", ".."); // web/site
export const SNAP_DIR = join(SITE_DIR, "seo-snapshots");
export const SITE_ORIGIN = "https://opentraces.ai";

// Priority pages the loop watches (URL paths).
export const PRIORITY_PAGES = ["/", "/docs", "/explorer", "/schema/latest"];
// A docs deep page kept in the gate so the per-doc metadata path stays covered.
export const DEEP_PAGES = ["/docs/getting-started/installation"];

// Map a URL path to its prerendered HTML file under <dist>/server/app.
export function htmlFileForPath(distDir, path) {
  const rel = path === "/" ? "index" : path.replace(/^\//, "");
  return join(SITE_DIR, distDir, "server", "app", `${rel}.html`);
}

// AI + search crawler user-agents, with the reverse-DNS host suffixes a
// *genuine* bot's PTR record must end with. Used to reject spoofed UA strings
// (anyone can send `User-Agent: GPTBot`; only OpenAI's IPs reverse-resolve to
// an openai.com host). Suffixes are intentionally permissive (the org domain)
// so a verified hit is high-precision without being brittle to subdomains.
export const AI_CRAWLERS = [
  { ua: "GPTBot", org: "OpenAI", verifyHost: [".openai.com"] },
  { ua: "OAI-SearchBot", org: "OpenAI", verifyHost: [".openai.com"] },
  { ua: "ChatGPT-User", org: "OpenAI", verifyHost: [".openai.com"] },
  { ua: "ClaudeBot", org: "Anthropic", verifyHost: [".anthropic.com"] },
  { ua: "Claude-SearchBot", org: "Anthropic", verifyHost: [".anthropic.com"] },
  { ua: "Claude-User", org: "Anthropic", verifyHost: [".anthropic.com"] },
  { ua: "PerplexityBot", org: "Perplexity", verifyHost: [".perplexity.ai", ".perplexitybot.com"] },
  { ua: "Perplexity-User", org: "Perplexity", verifyHost: [".perplexity.ai"] },
  { ua: "Google-Extended", org: "Google", verifyHost: [".googlebot.com", ".google.com"] },
  { ua: "Googlebot", org: "Google", verifyHost: [".googlebot.com", ".google.com"] },
  { ua: "Bingbot", org: "Microsoft", verifyHost: [".search.msn.com"] },
  { ua: "Bytespider", org: "ByteDance", verifyHost: [".bytedance.com", ".bytespider.com"] },
  { ua: "Amazonbot", org: "Amazon", verifyHost: [".crawl.amazon.com", ".amazon.com"] },
  { ua: "Applebot", org: "Apple", verifyHost: [".applebot.apple.com", ".apple.com"] },
  { ua: "CCBot", org: "CommonCrawl", verifyHost: [".commoncrawl.org"] },
  { ua: "Meta-ExternalAgent", org: "Meta", verifyHost: [".facebook.com", ".meta.com"] },
];

// Longest UA tokens first so e.g. "Claude-SearchBot" matches before "ClaudeBot".
const CRAWLERS_BY_LEN = [...AI_CRAWLERS].sort((a, b) => b.ua.length - a.ua.length);

export function matchCrawler(userAgent = "") {
  const ua = String(userAgent); // defensive: log fields can be arrays
  return CRAWLERS_BY_LEN.find((c) => ua.includes(c.ua)) ?? null;
}

// UTC date bucket for run identity (YYYY-MM-DD).
export function runDateBucket(d = new Date()) {
  return d.toISOString().slice(0, 10);
}

// Append one JSON object per line to a standings ledger (creates the dir if
// absent so a fresh checkout / different cwd never loses a computed record).
export function appendRecord(file, record) {
  mkdirSync(SNAP_DIR, { recursive: true });
  appendFileSync(join(SNAP_DIR, file), JSON.stringify(record) + "\n");
}

export function readRecords(file) {
  const path = join(SNAP_DIR, file);
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

// §3 idempotency: has a record for this (run_date_bucket, deploy_sha, surface)
// already been written? Lets a writer no-op a duplicate/late scheduler fire.
// Never coalesces an "unknown" deploy_sha (two distinct deploys that both failed
// the SHA lookup must not be treated as the same run).
export function recordExists(file, { run_date_bucket, deploy_sha, surface }) {
  if (deploy_sha === "unknown") return false;
  return readRecords(file).some(
    (r) => r.run_date_bucket === run_date_bucket && r.deploy_sha === deploy_sha && r.surface === surface,
  );
}

// The commit SHA of the deployed/working site. Prefer the CI/Vercel-provided
// SHA so a shallow/detached/headless checkout doesn't silently collapse to a
// single "unknown" key (which would make the idempotency key ambiguous).
export function currentDeploySha() {
  const env = process.env.VERCEL_GIT_COMMIT_SHA || process.env.GITHUB_SHA || process.env.GIT_COMMIT_SHA;
  if (env) return env.slice(0, 7);
  try {
    return execSync("git rev-parse --short HEAD", { cwd: SITE_DIR, encoding: "utf8" }).trim();
  } catch {
    return "unknown";
  }
}
