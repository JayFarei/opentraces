import { promises as fs } from "fs";
import path from "path";

// Storage backend for the early-access waitlist.
//
// Two interchangeable backends behind one interface:
//
//   - "kv":   Vercel KV (Upstash Redis) over its REST API, hit directly with
//             fetch so there is NO npm dependency to install and the build never
//             breaks. This is the production path. Provision a KV store in the
//             Vercel dashboard (Storage -> KV) and it injects KV_REST_API_URL +
//             KV_REST_API_TOKEN; the route picks this backend up automatically.
//
//   - "file": A local JSON file at .data/waitlist.json. This is a DEV-ONLY
//             fallback so the whole flow (form -> API -> stored -> admin read)
//             is provable end-to-end locally with zero external provisioning.
//             Serverless filesystems are not durable, so this backend is never
//             selected in production.
//
// In production with no KV configured, selectStore() returns null and the route
// answers 503 (honest "not configured yet") rather than silently dropping data.

export interface WaitlistStore {
  readonly backend: "kv" | "file";
  /** Add an email with a timestamp score. alreadyOnList=true if it was present. */
  add(email: string, ts: number): Promise<{ alreadyOnList: boolean }>;
  /** Total count + most-recent N as the flat [member, score, ...] WITHSCORES shape. */
  recent(limit: number): Promise<{ count: number; recent: (string | number)[] }>;
  /** Best-effort INCR with a TTL on first touch. Returns the new counter value. */
  incrWithTtl(key: string, ttlSeconds: number): Promise<number>;
}

// ---------------------------------------------------------------------------
// KV (Upstash Redis REST) backend
// ---------------------------------------------------------------------------

// The Upstash/Vercel Marketplace integration injects credentials under either
// the KV_REST_API_* prefix (legacy Vercel KV compatibility) or the
// UPSTASH_REDIS_REST_* prefix, depending on setup. Accept whichever is present.
const KV_URL = process.env.KV_REST_API_URL ?? process.env.UPSTASH_REDIS_REST_URL;
const KV_TOKEN = process.env.KV_REST_API_TOKEN ?? process.env.UPSTASH_REDIS_REST_TOKEN;

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

const kvStore: WaitlistStore = {
  backend: "kv",
  async add(email, ts) {
    // ZADD waitlist NX <ts> <email> -> returns 1 if added, 0 if already present.
    const added = await kv(["ZADD", "waitlist", "NX", ts, email]);
    return { alreadyOnList: added === 0 };
  },
  async recent(limit) {
    const count = await kv(["ZCARD", "waitlist"]);
    // REV orders high-score-first; indexes 0..limit-1 are the newest `limit`.
    const recent = await kv([
      "ZRANGE",
      "waitlist",
      "0",
      String(Math.max(0, limit - 1)),
      "REV",
      "WITHSCORES",
    ]);
    return {
      count: typeof count === "number" ? count : 0,
      recent: Array.isArray(recent) ? (recent as (string | number)[]) : [],
    };
  },
  async incrWithTtl(key, ttlSeconds) {
    const n = await kv(["INCR", key]);
    if (n === 1) await kv(["EXPIRE", key, ttlSeconds]);
    return typeof n === "number" ? n : 0;
  },
};

// ---------------------------------------------------------------------------
// File backend (dev fallback)
// ---------------------------------------------------------------------------

interface FileShape {
  // email -> first-seen epoch ms (mirrors a Redis sorted set keyed by email).
  emails: Record<string, number>;
}

const DATA_DIR = path.join(process.cwd(), ".data");
const DATA_FILE = path.join(DATA_DIR, "waitlist.json");

// Serialize read-modify-write so concurrent submissions in dev don't clobber.
let writeChain: Promise<unknown> = Promise.resolve();

// Rate-limit counters live in-process for the file backend. Dev is single
// process, and rate limiting is explicitly best-effort, so this is enough.
const rlCounters = new Map<string, { n: number; exp: number }>();

async function readFile(): Promise<FileShape> {
  try {
    const raw = await fs.readFile(DATA_FILE, "utf8");
    const parsed = JSON.parse(raw) as Partial<FileShape>;
    return { emails: parsed.emails ?? {} };
  } catch {
    return { emails: {} };
  }
}

async function writeFile(data: FileShape): Promise<void> {
  await fs.mkdir(DATA_DIR, { recursive: true });
  await fs.writeFile(DATA_FILE, JSON.stringify(data, null, 2), "utf8");
}

const fileStore: WaitlistStore = {
  backend: "file",
  add(email, ts) {
    const next = writeChain.then(async () => {
      const data = await readFile();
      const alreadyOnList = Object.prototype.hasOwnProperty.call(data.emails, email);
      if (!alreadyOnList) {
        data.emails[email] = ts;
        await writeFile(data);
      }
      return { alreadyOnList };
    });
    // Keep the chain alive but never let a rejection poison later writes.
    writeChain = next.catch(() => undefined);
    return next;
  },
  async recent(limit) {
    const data = await readFile();
    const sorted = Object.entries(data.emails).sort((a, b) => b[1] - a[1]);
    const recent: (string | number)[] = [];
    for (const [email, score] of sorted.slice(0, limit)) {
      recent.push(email, score);
    }
    return { count: sorted.length, recent };
  },
  async incrWithTtl(key, ttlSeconds) {
    const now = Date.now();
    const cur = rlCounters.get(key);
    if (!cur || cur.exp <= now) {
      rlCounters.set(key, { n: 1, exp: now + ttlSeconds * 1000 });
      return 1;
    }
    cur.n += 1;
    return cur.n;
  },
};

// ---------------------------------------------------------------------------
// Backend selection
// ---------------------------------------------------------------------------

/**
 * Returns the active store, or null when none is available (production with no
 * KV configured). KV wins when configured; otherwise the file store is used in
 * any non-production environment so local end-to-end testing works out of the box.
 */
export function selectStore(): WaitlistStore | null {
  if (KV_URL && KV_TOKEN) return kvStore;
  if (process.env.NODE_ENV !== "production") return fileStore;
  return null;
}
