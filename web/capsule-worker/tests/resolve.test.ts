import { describe, expect, it } from "vitest";
import {
  buildMarkdownFallbackUrl,
  buildResolveUrl,
  buildViewerUrl,
  CapsuleResolveError,
  parseCapsulePath,
  resolveCapsule,
} from "../src/resolve.js";
import { ENV_FACE_TOKENS, recordingFetcher } from "./helpers.js";
import { sampleRaw } from "./fixtures/sample.js";

describe("parseCapsulePath", () => {
  it("parses owner/repo/id@sha", () => {
    expect(parseCapsulePath("/acme/fetcher/64944d7c91aa87bd@abc123")).toMatchObject({
      owner: "acme",
      repo: "fetcher",
      id: "64944d7c91aa87bd",
      sha: "abc123",
      pinned: true,
      endpoint: null,
    });
  });

  it("defaults sha to main when @sha is omitted", () => {
    expect(parseCapsulePath("/acme/fetcher/64944d7c91aa87bd")).toMatchObject({
      sha: "main",
      pinned: false,
      endpoint: null,
    });
  });

  it("parses trailing endpoints", () => {
    expect(parseCapsulePath("/a/b/id@s/summary")?.endpoint).toBe("summary");
    expect(parseCapsulePath("/a/b/id@s/full")?.endpoint).toBe("full");
    expect(parseCapsulePath("/a/b/id@s/skill")?.endpoint).toBe("skill");
  });

  it("rejects unknown endpoints and malformed paths", () => {
    expect(parseCapsulePath("/a/b/id@s/bogus")).toBeNull();
    expect(parseCapsulePath("/a/b")).toBeNull();
    expect(parseCapsulePath("/")).toBeNull();
    expect(parseCapsulePath("/a/b/c/d/e")).toBeNull();
  });
});

describe("URL builders", () => {
  const ref = parseCapsulePath("/acme/fetcher/cap1@sha9")!;
  it("maps ref → the single HF resolve URL", () => {
    expect(buildResolveUrl(ref)).toBe(
      "https://huggingface.co/datasets/acme/fetcher/resolve/sha9/capsules/v1/cap1/capsule.json",
    );
  });
  it("maps ref → the md fallback and viewer URLs", () => {
    expect(buildMarkdownFallbackUrl(ref)).toBe(
      "https://huggingface.co/datasets/acme/fetcher/blob/sha9/capsules/v1/cap1/capsule.md",
    );
    expect(buildViewerUrl("https://capsules.opentraces.ai", ref)).toBe(
      "https://capsules.opentraces.ai/acme/fetcher/cap1@sha9",
    );
  });
  it("the resolve URL never names an environment-face sibling", () => {
    const url = buildResolveUrl(ref);
    for (const token of ENV_FACE_TOKENS) expect(url).not.toContain(token);
    expect(url.endsWith("/capsule.json")).toBe(true);
  });
});

describe("resolveCapsule", () => {
  const ref = parseCapsulePath("/acme/fetcher/cap1@sha9")!;

  it("fetches exactly ONE blob and parses it", async () => {
    const { fetcher, calls } = recordingFetcher();
    const resolved = await resolveCapsule(ref, { fetcher });
    expect(calls).toEqual([
      "https://huggingface.co/datasets/acme/fetcher/resolve/sha9/capsules/v1/cap1/capsule.json",
    ]);
    expect(resolved.raw).toBe(sampleRaw);
    expect(resolved.capsule.capsule_id).toBe("64944d7c91aa87bd");
  });

  it("throws CapsuleResolveError carrying the raw url on upstream 404", async () => {
    const { fetcher } = recordingFetcher("nope", { status: 404 });
    await expect(resolveCapsule(ref, { fetcher })).rejects.toMatchObject({
      kind: "upstream",
      status: 404,
      resolveUrl: "https://huggingface.co/datasets/acme/fetcher/resolve/sha9/capsules/v1/cap1/capsule.json",
    });
  });

  it("throws on network failure", async () => {
    const { fetcher } = recordingFetcher("", { throwErr: true });
    await expect(resolveCapsule(ref, { fetcher })).rejects.toBeInstanceOf(CapsuleResolveError);
  });

  it("throws a parse error on non-object JSON", async () => {
    const { fetcher } = recordingFetcher("[]");
    await expect(resolveCapsule(ref, { fetcher })).rejects.toMatchObject({ kind: "parse" });
  });
});
