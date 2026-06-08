import { NextRequest, NextResponse } from "next/server";

// Early-access waitlist endpoint.
//
// Storage: Vercel KV (Upstash Redis) via its REST API, hit directly with fetch so
// there is NO npm dependency to install and the build never breaks. Provision a KV
// store in the Vercel dashboard and it injects KV_REST_API_URL + KV_REST_API_TOKEN.
// Until then the endpoint returns 503 (the form shows a friendly message), so the
// site keeps building/deploying without the store configured.
//
// Dedup + timestamp in one op: ZADD waitlist NX <now> <email>  (NX => returns 0 if
// the email already exists). Admin read: GET /api/waitlist?token=WAITLIST_ADMIN_TOKEN.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const KV_URL = process.env.KV_REST_API_URL;
const KV_TOKEN = process.env.KV_REST_API_TOKEN;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

async function kv(cmd: (string | number)[]): Promise<unknown> {
  const res = await fetch(KV_URL as string, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${KV_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(cmd),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`kv ${res.status}`);
  return (await res.json()).result;
}

function notConfigured() {
  return NextResponse.json(
    { ok: false, error: "waitlist is not configured yet" },
    { status: 503 },
  );
}

export async function POST(req: NextRequest) {
  if (!KV_URL || !KV_TOKEN) return notConfigured();

  let email = "";
  try {
    const body = (await req.json()) as { email?: unknown };
    email = String(body?.email ?? "").trim().toLowerCase();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid request" }, { status: 400 });
  }

  if (!email || email.length > 254 || !EMAIL_RE.test(email)) {
    return NextResponse.json(
      { ok: false, error: "enter a valid email address" },
      { status: 400 },
    );
  }

  // Best-effort per-IP rate limit: 5 submissions / hour.
  const ip = (req.headers.get("x-forwarded-for") ?? "unknown").split(",")[0].trim();
  try {
    const n = await kv(["INCR", `wl:rl:${ip}`]);
    if (n === 1) await kv(["EXPIRE", `wl:rl:${ip}`, 3600]);
    if (typeof n === "number" && n > 5) {
      return NextResponse.json(
        { ok: false, error: "too many requests, try again later" },
        { status: 429 },
      );
    }
  } catch {
    /* rate limiting is best-effort; never block a signup on it */
  }

  try {
    const added = await kv(["ZADD", "waitlist", "NX", Date.now(), email]);
    return NextResponse.json({ ok: true, alreadyOnList: added === 0 });
  } catch {
    return NextResponse.json(
      { ok: false, error: "could not save right now, please try again" },
      { status: 500 },
    );
  }
}

export async function GET(req: NextRequest) {
  const adminToken = process.env.WAITLIST_ADMIN_TOKEN;
  const provided = req.nextUrl.searchParams.get("token");
  if (!adminToken || provided !== adminToken) {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }
  if (!KV_URL || !KV_TOKEN) return notConfigured();
  try {
    const count = await kv(["ZCARD", "waitlist"]);
    const recent = await kv(["ZRANGE", "waitlist", "-50", "-1", "REV", "WITHSCORES"]);
    return NextResponse.json({ ok: true, count, recent });
  } catch {
    return NextResponse.json({ ok: false, error: "read failed" }, { status: 500 });
  }
}
