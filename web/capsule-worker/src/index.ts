// opentraces capsule render worker.
//
// A stateless Cloudflare Worker that, given a capsule microsite URL, fetches the
// ONE immutable, already-redacted `capsule.json` from HuggingFace and serves it
// two ways: a human HTML page (Accept: text/html) and progressive JSON endpoints
// for no-CLI agents (/summary → /index → sections → /full, plus /skill). It is a
// pure projection over the frozen `opentraces.capsule.v1` envelope: no
// re-derivation, no re-redaction, one outbound fetch, and never the environment
// face.
//
// All request logic lives in `handleRequest`, which takes an injected
// {@link FetchLike} so the whole worker is testable with a fixture blob and no
// deployed infrastructure.

import { canonicalStringify } from "./canonical.js";
import { indexPayload, sectionPayload, summaryPayload } from "./endpoints.js";
import {
  renderAheadHtml,
  renderCapsuleHtml,
  renderDegradeHtml,
  renderNotFoundHtml,
} from "./render.js";
import {
  buildMarkdownFallbackUrl,
  buildResolveUrl,
  buildViewerUrl,
  CapsuleResolveError,
  parseCapsulePath,
  resolveCapsule,
  type CapsuleRef,
  type FetchLike,
} from "./resolve.js";
import { SKILL_TEXT, skillSha256 } from "./skill.js";
import { versionGate } from "./version.js";

export interface Env {
  CORS_ORIGIN?: string;
}

export interface HandleOptions {
  fetcher: FetchLike;
}

function corsHeaders(env: Env): Record<string, string> {
  return {
    "access-control-allow-origin": env.CORS_ORIGIN ?? "*",
    "access-control-allow-methods": "GET,OPTIONS",
    "access-control-allow-headers": "accept,content-type",
  };
}

function cacheHeader(ref: CapsuleRef | null): Record<string, string> {
  if (ref?.pinned) {
    return { "cache-control": "public, max-age=31536000, immutable" };
  }
  return { "cache-control": "public, max-age=300" };
}

function jsonResponse(payload: unknown, status: number, env: Env, ref: CapsuleRef | null): Response {
  return new Response(canonicalStringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...corsHeaders(env),
      ...cacheHeader(ref),
    },
  });
}

function rawJsonResponse(raw: string, env: Env, ref: CapsuleRef | null): Response {
  // Byte-identical pass-through: `/full` === the stored blob === `capsule open --json`.
  return new Response(raw, {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...corsHeaders(env),
      ...cacheHeader(ref),
    },
  });
}

function htmlResponse(html: string, status: number, env: Env, ref: CapsuleRef | null): Response {
  return new Response(html, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      ...corsHeaders(env),
      ...cacheHeader(ref),
    },
  });
}

function wantsHtml(request: Request): boolean {
  return (request.headers.get("accept") ?? "").includes("text/html");
}

export async function handleRequest(request: Request, env: Env, opts: HandleOptions): Promise<Response> {
  const method = request.method.toUpperCase();
  if (method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(env) });
  }
  if (method !== "GET" && method !== "HEAD") {
    return jsonResponse({ error: "method_not_allowed" }, 405, env, null);
  }

  const url = new URL(request.url);
  const ref = parseCapsulePath(url.pathname);
  if (!ref) {
    if (wantsHtml(request)) {
      return htmlResponse(renderNotFoundHtml(), 404, env, null);
    }
    return jsonResponse({ error: "not_found", detail: "path does not name a capsule" }, 404, env, null);
  }

  const viewerUrl = buildViewerUrl(url.origin, ref);

  // /skill is independent of capsule content — serve without any fetch.
  if (ref.endpoint === "skill") {
    return new Response(SKILL_TEXT, {
      status: 200,
      headers: {
        "content-type": "text/markdown; charset=utf-8",
        ...corsHeaders(env),
        ...cacheHeader(ref),
      },
    });
  }

  // The one outbound fetch.
  let resolved;
  try {
    resolved = await resolveCapsule(ref, { fetcher: opts.fetcher });
  } catch (error) {
    const resolveUrl = error instanceof CapsuleResolveError ? error.resolveUrl : buildResolveUrl(ref);
    const status = error instanceof CapsuleResolveError ? error.status : 502;
    const message = error instanceof Error ? error.message : String(error);
    const mdFallback = buildMarkdownFallbackUrl(ref);
    if (wantsHtml(request)) {
      return htmlResponse(renderDegradeHtml(resolveUrl, mdFallback, message), status, env, ref);
    }
    // Graceful degrade: even when the worker cannot render, agents get the raw URL.
    return jsonResponse(
      {
        error: "capsule_unavailable",
        detail: message,
        degrade: {
          raw_blob_url: resolveUrl,
          markdown_fallback_url: mdFallback,
          cli: `pipx run opentraces capsule open ${resolveUrl} --json`,
          message: "the capsule viewer is unavailable; fetch the immutable capsule.json directly",
        },
      },
      status,
      env,
      ref,
    );
  }

  // Rule 6: version-gate before rendering anything.
  const gate = versionGate(resolved.capsule.schema_version);
  if (gate.state !== "ok") {
    const rawBlobUrl = resolved.resolveUrl;
    if (gate.state === "ahead") {
      if (wantsHtml(request) && ref.endpoint === null) {
        return htmlResponse(renderAheadHtml(gate.found, rawBlobUrl), 200, env, ref);
      }
      return jsonResponse(
        { error: "capsule_schema_ahead", found: gate.found, raw_blob_url: rawBlobUrl, detail: "capsule schema is newer than this viewer" },
        409,
        env,
        ref,
      );
    }
    // not a capsule envelope at all
    if (wantsHtml(request) && ref.endpoint === null) {
      return htmlResponse(renderDegradeHtml(rawBlobUrl, buildMarkdownFallbackUrl(ref), `not an opentraces capsule (schema ${gate.found})`), 422, env, ref);
    }
    return jsonResponse({ error: "not_a_capsule", found: gate.found, raw_blob_url: rawBlobUrl }, 422, env, ref);
  }

  // Dispatch.
  switch (ref.endpoint) {
    case null:
      if (wantsHtml(request)) {
        return htmlResponse(renderCapsuleHtml(resolved, viewerUrl), 200, env, ref);
      }
      return jsonResponse(summaryPayload(resolved, viewerUrl, await skillSha256()), 200, env, ref);
    case "summary":
      return jsonResponse(summaryPayload(resolved, viewerUrl, await skillSha256()), 200, env, ref);
    case "index":
      return jsonResponse(indexPayload(resolved), 200, env, ref);
    case "slice":
    case "context":
    case "trail":
    case "repo":
    case "environment":
      return jsonResponse(sectionPayload(resolved, ref.endpoint), 200, env, ref);
    case "full":
      return rawJsonResponse(resolved.raw, env, ref);
    default:
      return jsonResponse({ error: "not_found" }, 404, env, ref);
  }
}

const globalFetcher: FetchLike = (input, init) => fetch(input, init);

export default {
  async fetch(request, env): Promise<Response> {
    return handleRequest(request, env, { fetcher: globalFetcher });
  },
} satisfies ExportedHandler<Env>;
