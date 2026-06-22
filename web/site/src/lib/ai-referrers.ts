// Referrer → channel classification for the "AI Search" analytics grouping
// (Phase 0 of SEO-AEO-LOOP.md). Behavioral traffic from AI answer engines is
// the lagging proof that the site is actually being surfaced; this maps a
// referrer hostname to a channel so the monitoring loop can read an "AI Search"
// share out of the self-hosted analytics.
//
// Matching is exact-or-subdomain ONLY (host === h || host.endsWith("." + h)) —
// never a bare substring — so "myopenai.com.evil.net" does NOT classify as AI.
//
// Wiring note: the analytics tracker (lazyanalytics) lives in a separate worker
// and is what records `document.referrer`. To light up the channel, classify
// the referrer host there (or post-hoc over its event export) with
// `classifyReferrer()`. This module is the single source of truth for the
// domain lists so the worker and the loop agree. Host-only referrers cannot
// distinguish path-based AI surfaces (e.g. Bing Copilot under bing.com/chat);
// those are unobservable from `document.referrer` and are intentionally absent.

export type ReferrerChannel = "ai_search" | "search" | "social" | "direct_or_other";

// AI answer engines (the channel this loop most cares about). Exact hosts only.
export const AI_SEARCH_HOSTS = [
  "chatgpt.com",
  "chat.openai.com",
  "openai.com",
  "perplexity.ai",
  "gemini.google.com",
  "claude.ai",
  "copilot.microsoft.com",
  "you.com",
  "phind.com",
];

// Traditional search engines. Exact hosts only.
export const SEARCH_HOSTS = [
  "bing.com",
  "duckduckgo.com",
  "ecosia.org",
  "search.brave.com",
  "search.yahoo.com",
  "baidu.com",
  "kagi.com",
];

// ccTLD search families (google.com, google.co.uk, yandex.ru, …): match the
// registrable family at a label boundary, not as a free-floating substring.
export const SEARCH_FAMILIES = ["google", "yandex"];

// Social / community surfaces (off-site lever surfaces from §4a).
export const SOCIAL_HOSTS = [
  "news.ycombinator.com",
  "reddit.com",
  "x.com",
  "twitter.com",
  "t.co",
  "linkedin.com",
  "github.com",
  "bsky.app",
  "lobste.rs",
];

function hostFromReferrer(referrer: string): string {
  try {
    const u = new URL(referrer);
    // Only http(s) referrers carry a meaningful host (android-app:// etc. parse
    // the package name as the hostname and would misclassify).
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    return u.hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    // Already a bare host.
    return referrer.toLowerCase().replace(/^www\./, "");
  }
}

const matches = (host: string, h: string) => host === h || host.endsWith("." + h);
const matchesFamily = (host: string, fam: string) =>
  host === fam || host.startsWith(fam + ".") || host.includes("." + fam + ".");

export function classifyReferrer(referrer: string | null | undefined): ReferrerChannel {
  if (!referrer) return "direct_or_other";
  const host = hostFromReferrer(referrer);
  if (!host) return "direct_or_other";
  // AI engines first: gemini.google.com must beat the google search family.
  if (AI_SEARCH_HOSTS.some((h) => matches(host, h))) return "ai_search";
  if (SEARCH_HOSTS.some((h) => matches(host, h))) return "search";
  if (SEARCH_FAMILIES.some((f) => matchesFamily(host, f))) return "search";
  if (SOCIAL_HOSTS.some((h) => matches(host, h))) return "social";
  return "direct_or_other";
}
