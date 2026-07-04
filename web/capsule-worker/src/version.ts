// Rule 6: version-gate (reject-newer).
//
// The worker understands exactly one frozen schema. A newer
// `opentraces.capsule.<x>` must degrade to "needs a newer viewer" rather than
// mis-render an envelope shape it does not know. A schema that is not a capsule
// schema at all is a hard error. This mirrors the CLI's `validate_capsule`
// reject-newer and the `_discovery.version_rule` published in the data contract.

export const CAPSULE_SCHEMA_VERSION = "opentraces.capsule.v1";
const CAPSULE_SCHEMA_PREFIX = "opentraces.capsule.";

export type VersionGate =
  | { state: "ok" }
  | { state: "ahead"; found: string }
  | { state: "not_capsule"; found: string };

/**
 * Classify an envelope's `schema_version` against the one version this worker
 * renders. `ahead` means a same-family but different (assume newer) version;
 * `not_capsule` means it is not an `opentraces.capsule.*` envelope at all.
 */
export function versionGate(schemaVersion: unknown): VersionGate {
  const found = typeof schemaVersion === "string" ? schemaVersion : String(schemaVersion);
  if (found === CAPSULE_SCHEMA_VERSION) {
    return { state: "ok" };
  }
  if (typeof schemaVersion === "string" && schemaVersion.startsWith(CAPSULE_SCHEMA_PREFIX)) {
    return { state: "ahead", found };
  }
  return { state: "not_capsule", found };
}
