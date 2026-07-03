import { describe, expect, it } from "vitest";
import { BASE_PATH, BASE_RESOLVE_URL, callHandler } from "./helpers.js";

// Graceful degrade: when the worker cannot fetch/parse the blob it must still
// point agents at the immutable raw URL, and must never throw. The raw HF
// /resolve/ blob serves agents even when the worker is unavailable.
describe("graceful degrade (JSON)", () => {
  it("network failure → 502 carrying the raw blob url", async () => {
    const { res } = await callHandler(`${BASE_PATH}/summary`, { throwErr: true });
    expect(res.status).toBe(502);
    const body = (await res.json()) as any;
    expect(body.error).toBe("capsule_unavailable");
    expect(body.degrade.raw_blob_url).toBe(BASE_RESOLVE_URL);
    expect(body.degrade.markdown_fallback_url).toContain("/blob/abc123/");
    expect(body.degrade.cli).toContain("pipx run opentraces capsule open");
  });

  it("upstream 404 → 404 with degrade pointer", async () => {
    const { res } = await callHandler(`${BASE_PATH}/full`, { status: 404, body: "not found" });
    expect(res.status).toBe(404);
    const body = (await res.json()) as any;
    expect(body.degrade.raw_blob_url).toBe(BASE_RESOLVE_URL);
  });

  it("upstream 500 → 502 with degrade pointer", async () => {
    const { res } = await callHandler(`${BASE_PATH}/index`, { status: 500, body: "boom" });
    expect(res.status).toBe(502);
    const body = (await res.json()) as any;
    expect(body.degrade.raw_blob_url).toBe(BASE_RESOLVE_URL);
  });
});

describe("graceful degrade (HTML)", () => {
  it("network failure → an HTML page naming the raw blob url and CLI", async () => {
    const { res } = await callHandler(BASE_PATH, { accept: "text/html", throwErr: true });
    expect(res.status).toBe(502);
    expect(res.headers.get("content-type")).toContain("text/html");
    const html = await res.text();
    expect(html).toContain(BASE_RESOLVE_URL);
    expect(html).toContain("capsule open");
  });
});
