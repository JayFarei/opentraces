// The one load-bearing resolver.
//
// A capsule microsite URL has the form
//   capsules.opentraces.ai/<owner>/<repo>/<id>[@<sha>][/<endpoint>]
// The worker maps that to the single canonical HuggingFace resolve URL
//   huggingface.co/datasets/<owner>/<repo>/resolve/<sha>/capsules/v1/<id>/capsule.json
// fetches it ONCE, and renders. It is a pure projection: no re-derivation, no
// re-redaction, and — critically — it fetches nothing except the one immutable,
// already-redacted `capsule.json`. It never fetches the heavy "environment
// face" siblings (capsule.bundle.tar.gz, runtime.json, lock.txt, pin.json).

export type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

export const CAPSULE_ENDPOINTS = [
  "summary",
  "index",
  "slice",
  "context",
  "trail",
  "repo",
  "environment",
  "full",
  "skill",
] as const;

export type CapsuleEndpoint = (typeof CAPSULE_ENDPOINTS)[number];

export interface CapsuleRef {
  owner: string;
  repo: string;
  id: string;
  /** The pinned revision; `main` when the URL omitted `@<sha>`. */
  sha: string;
  /** True when the URL carried an explicit `@<sha>` (→ immutable, cache hard). */
  pinned: boolean;
  /** The requested progressive endpoint, or null for the base (human) page. */
  endpoint: CapsuleEndpoint | null;
}

function isEndpoint(value: string): value is CapsuleEndpoint {
  return (CAPSULE_ENDPOINTS as readonly string[]).includes(value);
}

/**
 * Parse a microsite pathname into a {@link CapsuleRef}. Returns null when the
 * path does not name a capsule (so the caller can 404 without guessing).
 */
export function parseCapsulePath(pathname: string): CapsuleRef | null {
  const segments = pathname.split("/").filter((s) => s.length > 0);
  if (segments.length < 3 || segments.length > 4) {
    return null;
  }

  const [owner, repo, idAtSha, maybeEndpoint] = segments;
  let endpoint: CapsuleEndpoint | null = null;
  if (segments.length === 4) {
    if (!isEndpoint(maybeEndpoint)) {
      return null;
    }
    endpoint = maybeEndpoint;
  }

  const atIndex = idAtSha.indexOf("@");
  const id = atIndex === -1 ? idAtSha : idAtSha.slice(0, atIndex);
  const shaPart = atIndex === -1 ? "" : idAtSha.slice(atIndex + 1);
  if (!owner || !repo || !id) {
    return null;
  }

  return {
    owner,
    repo,
    id,
    sha: shaPart || "main",
    pinned: shaPart.length > 0,
    endpoint,
  };
}

/** The canonical HF resolve URL for the immutable `capsule.json` blob. */
export function buildResolveUrl(ref: CapsuleRef): string {
  return `https://huggingface.co/datasets/${ref.owner}/${ref.repo}/resolve/${ref.sha}/capsules/v1/${ref.id}/capsule.json`;
}

/** The HF blob URL for the human-readable `capsule.md` graceful-degrade fallback. */
export function buildMarkdownFallbackUrl(ref: CapsuleRef): string {
  return `https://huggingface.co/datasets/${ref.owner}/${ref.repo}/blob/${ref.sha}/capsules/v1/${ref.id}/capsule.md`;
}

/** The microsite's own canonical viewer URL for this capsule. */
export function buildViewerUrl(origin: string, ref: CapsuleRef): string {
  const suffix = ref.pinned ? `@${ref.sha}` : "";
  return `${origin}/${ref.owner}/${ref.repo}/${ref.id}${suffix}`;
}

export class CapsuleResolveError extends Error {
  constructor(
    message: string,
    readonly kind: "upstream" | "parse",
    readonly status: number,
    readonly resolveUrl: string,
  ) {
    super(message);
    this.name = "CapsuleResolveError";
  }
}

export interface ResolvedCapsule {
  ref: CapsuleRef;
  /** The exact upstream bytes — used verbatim by the `/full` endpoint. */
  raw: string;
  /** The parsed envelope. */
  capsule: Record<string, unknown>;
  resolveUrl: string;
}

/**
 * Fetch and parse the one immutable `capsule.json` for a ref. Makes EXACTLY one
 * outbound request (to {@link buildResolveUrl}); never touches a sibling
 * artifact. On any upstream failure or parse failure it throws a
 * {@link CapsuleResolveError} carrying the raw resolve URL so the caller can
 * degrade gracefully (agents can still fetch that URL directly).
 */
export async function resolveCapsule(
  ref: CapsuleRef,
  opts: { fetcher: FetchLike },
): Promise<ResolvedCapsule> {
  const resolveUrl = buildResolveUrl(ref);

  let response: Response;
  try {
    response = await opts.fetcher(resolveUrl, {
      headers: { accept: "application/json" },
    });
  } catch (error) {
    throw new CapsuleResolveError(
      `upstream fetch failed: ${error instanceof Error ? error.message : String(error)}`,
      "upstream",
      502,
      resolveUrl,
    );
  }

  if (!response.ok) {
    throw new CapsuleResolveError(
      `upstream returned ${response.status}`,
      "upstream",
      response.status === 404 ? 404 : 502,
      resolveUrl,
    );
  }

  const raw = await response.text();
  let capsule: unknown;
  try {
    capsule = JSON.parse(raw);
  } catch {
    throw new CapsuleResolveError("capsule.json is not valid JSON", "parse", 502, resolveUrl);
  }
  if (typeof capsule !== "object" || capsule === null || Array.isArray(capsule)) {
    throw new CapsuleResolveError("capsule.json is not a JSON object", "parse", 502, resolveUrl);
  }

  return { ref, raw, capsule: capsule as Record<string, unknown>, resolveUrl };
}
