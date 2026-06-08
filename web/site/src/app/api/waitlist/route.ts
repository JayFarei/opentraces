import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual } from "crypto";
import { selectStore } from "@/lib/waitlist-store";

// Early-access waitlist endpoint.
//
// Storage is pluggable (see lib/waitlist-store.ts): Vercel KV (Upstash Redis)
// in production, a local JSON file in dev so the flow is provable end-to-end
// with zero provisioning. When no store is available (production without KV)
// the endpoint returns 503 and the form shows a friendly message, so the site
// keeps building/deploying without the store configured.
//
// Anti-bot ("antibody"): a hidden honeypot field plus a best-effort per-IP rate
// limit. Bots that fill the honeypot get a silent fake-success and are never
// stored. Admin read: GET /api/waitlist with an "Authorization: Bearer <token>"
// header (preferred — keeps the secret out of URLs/logs) or ?token=<token>.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Constant-time token check. Length is compared first (timingSafeEqual throws on
// unequal-length buffers); the length of a random token is not itself a secret.
function tokenMatches(provided: string | null, expected: string): boolean {
  if (!provided) return false;
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

function notConfigured() {
  return NextResponse.json(
    { ok: false, error: "waitlist is not configured yet" },
    { status: 503 },
  );
}

export async function POST(req: NextRequest) {
  const store = selectStore();
  if (!store) return notConfigured();

  let email = "";
  let honeypot = "";
  try {
    const body = (await req.json()) as { email?: unknown; company?: unknown };
    email = String(body?.email ?? "").trim().toLowerCase();
    // Honeypot field. Real users never see or fill it; bots that auto-fill
    // every input give themselves away here.
    honeypot = String(body?.company ?? "").trim();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid request" }, { status: 400 });
  }

  // Antibody: if the honeypot is filled, pretend it worked but store nothing.
  // Returning success keeps the bot from probing for the real validation rules.
  if (honeypot) {
    return NextResponse.json({ ok: true, alreadyOnList: false });
  }

  if (!email || email.length > 254 || !EMAIL_RE.test(email)) {
    return NextResponse.json(
      { ok: false, error: "enter a valid email address" },
      { status: 400 },
    );
  }

  // Best-effort per-IP rate limit: default 5 submissions / hour. The limit is
  // env-tunable (WAITLIST_RATE_LIMIT) so test/ops can raise it without weakening
  // the production default.
  const limit = Number(process.env.WAITLIST_RATE_LIMIT) || 5;
  const ip = (req.headers.get("x-forwarded-for") ?? "unknown").split(",")[0].trim();
  try {
    const n = await store.incrWithTtl(`wl:rl:${ip}`, 3600);
    if (n > limit) {
      return NextResponse.json(
        { ok: false, error: "too many requests, try again later" },
        { status: 429 },
      );
    }
  } catch {
    /* rate limiting is best-effort; never block a signup on it */
  }

  try {
    const { alreadyOnList } = await store.add(email, Date.now());
    return NextResponse.json({ ok: true, alreadyOnList });
  } catch {
    return NextResponse.json(
      { ok: false, error: "could not save right now, please try again" },
      { status: 500 },
    );
  }
}

export async function GET(req: NextRequest) {
  const adminToken = process.env.WAITLIST_ADMIN_TOKEN;
  // Prefer the Authorization: Bearer header so the secret never lands in access
  // logs / browser history / Referer. Fall back to ?token= for convenience.
  const bearer = req.headers.get("authorization")?.match(/^Bearer\s+(.+)$/i)?.[1]?.trim();
  const provided = bearer ?? req.nextUrl.searchParams.get("token");
  if (!adminToken || !tokenMatches(provided, adminToken)) {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }
  const store = selectStore();
  if (!store) return notConfigured();
  try {
    const { count, recent } = await store.recent(50);
    return NextResponse.json({ ok: true, count, recent, backend: store.backend });
  } catch {
    return NextResponse.json({ ok: false, error: "read failed" }, { status: 500 });
  }
}
