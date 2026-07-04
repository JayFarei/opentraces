import { describe, expect, it } from "vitest";
import { versionGate } from "../src/version.js";

// Rule 6: version-gate (reject-newer).
describe("versionGate", () => {
  it("accepts exactly opentraces.capsule.v1", () => {
    expect(versionGate("opentraces.capsule.v1")).toEqual({ state: "ok" });
  });

  it("rejects a newer same-family schema as ahead", () => {
    expect(versionGate("opentraces.capsule.v2")).toEqual({ state: "ahead", found: "opentraces.capsule.v2" });
    expect(versionGate("opentraces.capsule.v99")).toMatchObject({ state: "ahead" });
    expect(versionGate("opentraces.capsule.vNEXT")).toMatchObject({ state: "ahead" });
  });

  it("classifies non-capsule schemas as not_capsule", () => {
    expect(versionGate("opentraces.trace_slice.v1")).toMatchObject({ state: "not_capsule" });
    expect(versionGate("something-else")).toMatchObject({ state: "not_capsule" });
    expect(versionGate(undefined)).toMatchObject({ state: "not_capsule" });
    expect(versionGate(42)).toMatchObject({ state: "not_capsule" });
  });
});
