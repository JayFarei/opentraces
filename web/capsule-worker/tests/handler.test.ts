import { describe, expect, it } from "vitest";
import { BASE_PATH, BASE_RESOLVE_URL, ENV_FACE_TOKENS, callHandler, mutate } from "./helpers.js";
import { sampleRaw } from "./fixtures/sample.js";

const JSON_ENDPOINTS = ["", "/summary", "/index", "/slice", "/context", "/trail", "/repo", "/environment", "/full"];

describe("end-to-end routing over an injected fixture blob (no deployed infra)", () => {
  it("/full is byte-identical to the stored envelope (issue #199 acceptance)", async () => {
    const { res } = await callHandler(`${BASE_PATH}/full`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.text()).toBe(sampleRaw); // exact pass-through == capsule open --json
  });

  it("every endpoint makes EXACTLY one fetch and never touches the environment face", async () => {
    for (const suffix of JSON_ENDPOINTS) {
      const { res, calls } = await callHandler(`${BASE_PATH}${suffix}`);
      expect(res.status, `status for ${suffix || "/(base)"}`).toBe(200);
      expect(calls, `fetch count for ${suffix || "/(base)"}`).toEqual([BASE_RESOLVE_URL]);
      for (const token of ENV_FACE_TOKENS) {
        expect(calls[0], `no env-face token '${token}' in fetch url`).not.toContain(token);
      }
    }
  });

  it("base path negotiates HTML vs JSON", async () => {
    const html = await callHandler(BASE_PATH, { accept: "text/html" });
    expect(html.res.headers.get("content-type")).toContain("text/html");
    expect(await html.res.text()).toContain("captured, untrusted content");

    const json = await callHandler(BASE_PATH, { accept: "application/json" });
    expect(json.res.headers.get("content-type")).toContain("application/json");
    const body = (await json.res.json()) as any;
    expect(body._discovery.schema).toBe("opentraces.capsule.v1");
    expect(body.capsule_id).toBe("64944d7c91aa87bd");
  });

  it("pins immutable cache-control only when @sha is present", async () => {
    const pinned = await callHandler(`${BASE_PATH}/summary`);
    expect(pinned.res.headers.get("cache-control")).toContain("immutable");
    const unpinned = await callHandler("/acme/fetcher/64944d7c91aa87bd/summary");
    expect(unpinned.res.headers.get("cache-control")).not.toContain("immutable");
  });

  it("sets CORS headers so agents can read cross-origin", async () => {
    const { res } = await callHandler(`${BASE_PATH}/full`);
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
    const options = await callHandler(BASE_PATH, { accept: "*/*" });
    // OPTIONS is exercised separately below
    expect(options.res.status).toBe(200);
  });
});

describe("version-gate through the handler (rule 6)", () => {
  const aheadBody = mutate((c) => (c.schema_version = "opentraces.capsule.v2"));

  it("JSON endpoint on a newer schema → 409 capsule_schema_ahead", async () => {
    const { res } = await callHandler(`${BASE_PATH}/summary`, { body: aheadBody });
    expect(res.status).toBe(409);
    const body = (await res.json()) as any;
    expect(body.error).toBe("capsule_schema_ahead");
    expect(body.found).toBe("opentraces.capsule.v2");
    expect(body.raw_blob_url).toBe(BASE_RESOLVE_URL);
  });

  it("HTML base on a newer schema → 'needs a newer viewer' page", async () => {
    const { res } = await callHandler(BASE_PATH, { accept: "text/html", body: aheadBody });
    expect(res.status).toBe(200);
    const html = await res.text();
    expect(html).toContain("Needs a newer viewer");
    expect(html).toContain(BASE_RESOLVE_URL);
  });

  it("a non-capsule schema → 422 not_a_capsule", async () => {
    const body = mutate((c) => (c.schema_version = "opentraces.trace_slice.v1"));
    const { res } = await callHandler(`${BASE_PATH}/full`, { body });
    expect(res.status).toBe(422);
    expect(((await res.json()) as any).error).toBe("not_a_capsule");
  });
});

describe("handler-level XSS escaping (rule 1)", () => {
  it("escapes an injected script tag in the rendered HTML", async () => {
    const body = mutate((c) => (c.intent.headline = "<script>alert('pwn')</script>"));
    const { res } = await callHandler(BASE_PATH, { accept: "text/html", body });
    const html = await res.text();
    expect(html).not.toContain("<script>alert('pwn')</script>");
    expect(html).toContain("&lt;script&gt;alert(&#39;pwn&#39;)&lt;/script&gt;");
  });
});

describe("not-found and method handling", () => {
  it("a path that is not a capsule → 404", async () => {
    const { res } = await callHandler("/just/two");
    expect(res.status).toBe(404);
  });

  it("OPTIONS → 204 with CORS", async () => {
    const req = new Request(`https://capsules.opentraces.ai${BASE_PATH}`, { method: "OPTIONS" });
    const { handleRequest } = await import("../src/index.js");
    const res = await handleRequest(req, { CORS_ORIGIN: "*" }, { fetcher: async () => new Response("") });
    expect(res.status).toBe(204);
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
  });
});
