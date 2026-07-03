import { describe, expect, it } from "vitest";
import { canonicalStringify } from "../src/canonical.js";
import { indexPayload, sectionPayload, summaryPayload } from "../src/endpoints.js";
import type { ResolvedCapsule } from "../src/resolve.js";
import { ENV_FACE_TOKENS } from "./helpers.js";
import { sampleCapsule, sampleRaw } from "./fixtures/sample.js";

const resolved = {
  ref: { owner: "acme", repo: "fetcher", id: "64944d7c91aa87bd", sha: "abc123", pinned: true, endpoint: null },
  raw: sampleRaw,
  capsule: sampleCapsule,
  resolveUrl: "",
} as unknown as ResolvedCapsule;

describe("section endpoints carry byte-identical sub-objects (issue #199 acceptance)", () => {
  const cases = [
    ["slice", "slice"],
    ["context", "context_resume_packet"],
    ["trail", "trail_anchors"],
    ["repo", "repo_pin"],
    ["environment", "environment"],
  ] as const;

  for (const [endpoint, key] of cases) {
    it(`/${endpoint} passes ${key} through unchanged and byte-identically`, () => {
      const payload = sectionPayload(resolved, endpoint) as any;
      // value identity
      expect(payload[key]).toEqual((sampleCapsule as any)[key]);
      // framing keys
      expect(payload.schema_version).toBe("opentraces.capsule.v1");
      expect(payload.capsule_id).toBe("64944d7c91aa87bd");
      expect(payload.content_is_untrusted).toBe(true);
      // byte identity of the carried core to the CLI serialization
      expect(canonicalStringify(payload[key])).toBe(canonicalStringify((sampleCapsule as any)[key]));
    });
  }
});

describe("/environment never leaks the heavy environment face", () => {
  it("carries only the name-only projection keys", () => {
    const payload = sectionPayload(resolved, "environment") as any;
    const env = payload.environment;
    expect(Object.keys(env).sort()).toEqual(["consumes", "dependencies", "language_ecosystem", "setup"]);
    const serialized = canonicalStringify(payload);
    for (const token of ENV_FACE_TOKENS) expect(serialized).not.toContain(token);
  });
});

describe("/summary", () => {
  const s = summaryPayload(resolved, "https://capsules.opentraces.ai/acme/fetcher/64944d7c91aa87bd@abc123", "deadbeefcafe") as any;

  it("carries the intent headline and verbatim failing_step", () => {
    expect(s.intent_headline).toBe((sampleCapsule as any).intent.headline);
    expect(s.failing_step).toEqual((sampleCapsule as any).failing_step);
    expect(s.outcome_taxonomy).toBe("unclear");
  });

  it("renders the verdict as a claim, never proof (rule 5)", () => {
    expect(s.recorded_verdict.claim_not_proof).toBe(true);
    expect(s.recorded_verdict.render_state_replay).toBe("replay_unverified");
    // never invents a fixed/reproduces state on a v1 envelope
    expect(s.recorded_verdict).not.toHaveProperty("state");
  });

  it("carries a content-addressed _discovery block", () => {
    expect(s._discovery.schema).toBe("opentraces.capsule.v1");
    expect(s._discovery.content_is_untrusted).toBe(true);
    expect(s._discovery.skill).toBe("https://capsules.opentraces.ai/acme/fetcher/64944d7c91aa87bd@abc123/skill");
    expect(s._discovery.skill_sha256).toBe("deadbeefcafe");
    expect(s._discovery.skill_version).toBe("opentraces-capsule.v1");
    expect(s._discovery.pipx).toContain("pipx run opentraces");
    expect(s._discovery.version_rule).toContain("reject-newer");
  });
});

describe("/index", () => {
  const i = indexPayload(resolved) as any;

  it("surfaces real byte sizes per section", () => {
    const full = i.sections.find((x: any) => x.endpoint === "/full");
    expect(full.bytes).toBe(sampleRaw.length);
    const repo = i.sections.find((x: any) => x.endpoint === "/repo");
    expect(repo.bytes).toBe(canonicalStringify((sampleCapsule as any).repo_pin).length);
  });

  it("renders limitations verbatim (rule 4) and privacy scope (rule 3)", () => {
    expect(i.limitations).toEqual((sampleCapsule as any).limitations);
    expect(i.privacy_scope).toEqual((sampleCapsule as any).privacy_scope);
  });

  it("keeps the redaction manifest counts-only — never matched text (rule 2)", () => {
    const m = i.redaction.manifest;
    expect(m.redactions_applied).toBe(12);
    expect(m.findings_total).toBe(12);
    expect(m.floor_satisfied).toBe(true);
    // detail maps that could carry field bytes are dropped
    expect(m).not.toHaveProperty("by_field_path");
    expect(m).not.toHaveProperty("excluded_field_paths");
    expect(JSON.stringify(i)).not.toContain("matched_text");
  });
});
