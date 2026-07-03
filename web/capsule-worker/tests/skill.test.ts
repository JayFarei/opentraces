import { describe, expect, it } from "vitest";
import { SKILL_TEXT, SKILL_VERSION, skillSha256 } from "../src/skill.js";
import { BASE_PATH, callHandler } from "./helpers.js";

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

describe("content-addressed skill", () => {
  it("hashes the actual skill text (content-addressed, not a stub)", async () => {
    const expected = await sha256Hex(SKILL_TEXT);
    expect(await skillSha256()).toBe(expected);
    expect(expected).toMatch(/^[0-9a-f]{64}$/);
  });

  it("is stable across calls but changes when the text changes", async () => {
    expect(await skillSha256()).toBe(await skillSha256());
    expect(await skillSha256()).not.toBe(await sha256Hex(SKILL_TEXT + "x"));
  });

  it("carries the version + reject-newer guidance in the text", () => {
    expect(SKILL_VERSION).toBe("opentraces-capsule.v1");
    expect(SKILL_TEXT).toContain("opentraces.capsule.v1");
    expect(SKILL_TEXT).toContain("reject-newer");
  });
});

describe("GET /skill", () => {
  it("serves the skill as markdown WITHOUT fetching the capsule", async () => {
    const { res, calls } = await callHandler(`${BASE_PATH}/skill`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/markdown");
    expect(calls).toEqual([]); // no capsule fetch needed
    expect(await res.text()).toContain("opentraces-capsule");
  });
});
