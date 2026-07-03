import { describe, expect, it } from "vitest";
import { canonicalStringify } from "../src/canonical.js";
import { environmentCanonical, fullCanonical, repoPinCanonical } from "./fixtures/golden.js";
import { sampleCapsule, sampleRaw } from "./fixtures/sample.js";

// canonicalStringify must be byte-identical to Python
// json.dumps(obj, sort_keys=True, indent=2) — the exact serializer the CLI uses
// to write capsule.json. The frozen sample is stored in that form, so it is the
// cross-language oracle: if canonicalStringify(sample) === the stored bytes, the
// worker's JSON endpoints are byte-identical to the CLI envelope.
describe("canonicalStringify byte-parity with the CLI serializer", () => {
  it("reproduces the whole frozen envelope byte-for-byte", () => {
    expect(canonicalStringify(sampleCapsule)).toBe(sampleRaw);
    expect(canonicalStringify(sampleCapsule)).toBe(fullCanonical);
  });

  it("reproduces carried sub-objects (repo_pin, environment)", () => {
    expect(canonicalStringify((sampleCapsule as any).repo_pin)).toBe(repoPinCanonical);
    expect(canonicalStringify((sampleCapsule as any).environment)).toBe(environmentCanonical);
  });

  it("matches Python string/container escaping rules", () => {
    expect(canonicalStringify("…")).toBe('"\\u2026"'); // ensure_ascii
    expect(canonicalStringify("\x7f")).toBe('"\\u007f"'); // 0x7f escaped like Python
    expect(canonicalStringify("~")).toBe('"~"'); // 0x7e raw
    expect(canonicalStringify("\t\n\r\b\f")).toBe('"\\t\\n\\r\\b\\f"');
    expect(canonicalStringify("\x01")).toBe('"\\u0001"');
    expect(canonicalStringify("a/b")).toBe('"a/b"'); // slash NOT escaped
    expect(canonicalStringify([])).toBe("[]");
    expect(canonicalStringify({})).toBe("{}");
    expect(canonicalStringify({ b: 1, a: 2 })).toBe('{\n  "a": 2,\n  "b": 1\n}');
  });

  it("refuses non-finite numbers rather than diverge", () => {
    expect(() => canonicalStringify(Number.POSITIVE_INFINITY)).toThrow();
  });
});
