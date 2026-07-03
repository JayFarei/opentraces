import { handleRequest, type Env } from "../src/index.js";
import type { FetchLike } from "../src/resolve.js";
import { sampleRaw } from "./fixtures/sample.js";

export const ENV: Env = { CORS_ORIGIN: "*" };

/** A pinned capsule path used across the suite. */
export const BASE_PATH = "/acme/fetcher/64944d7c91aa87bd@abc123";

/** The HF resolve URL the worker must map BASE_PATH to. */
export const BASE_RESOLVE_URL =
  "https://huggingface.co/datasets/acme/fetcher/resolve/abc123/capsules/v1/64944d7c91aa87bd/capsule.json";

/**
 * Substrings that would betray the worker fetching or serving the heavy
 * "environment face" siblings instead of the one name-only projection.
 */
export const ENV_FACE_TOKENS = [
  "bundle",
  ".tar.gz",
  "runtime.json",
  "lock.txt",
  "pin.json",
  "/21-environment",
];

export interface RecordingFetcher {
  fetcher: FetchLike;
  calls: string[];
}

export function recordingFetcher(
  body: string = sampleRaw,
  init: { status?: number; throwErr?: boolean } = {},
): RecordingFetcher {
  const calls: string[] = [];
  const fetcher: FetchLike = async (url) => {
    calls.push(String(url));
    if (init.throwErr) throw new Error("network down");
    return new Response(body, { status: init.status ?? 200 });
  };
  return { fetcher, calls };
}

/** Deep-clone the frozen sample, mutate it, and re-serialize (non-canonical is fine as worker input). */
export function mutate(fn: (capsule: any) => void): string {
  const capsule = JSON.parse(sampleRaw);
  fn(capsule);
  return JSON.stringify(capsule);
}

export interface HandlerResult {
  res: Response;
  calls: string[];
}

export async function callHandler(
  path: string,
  opts: { accept?: string; body?: string; status?: number; throwErr?: boolean } = {},
): Promise<HandlerResult> {
  const { fetcher, calls } = recordingFetcher(opts.body, { status: opts.status, throwErr: opts.throwErr });
  const req = new Request(`https://capsules.opentraces.ai${path}`, {
    headers: { accept: opts.accept ?? "application/json" },
  });
  const res = await handleRequest(req, ENV, { fetcher });
  return { res, calls };
}
