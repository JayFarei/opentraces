// Progressive JSON endpoints — thin projections carved from the one envelope.
//
// Every payload is a read-only slice of the frozen `opentraces.capsule.v1`
// envelope. No endpoint invents a field: framing keys (schema_version,
// capsule_id, content_is_untrusted) plus `_discovery` are namespaced/known, and
// every other value is a byte-faithful pass-through of a carried sub-object.
// `/full` returns the upstream bytes verbatim so it is byte-identical to
// `opentraces capsule open --json`.

import { canonicalStringify } from "./canonical.js";
import { discoveryBlock } from "./discovery.js";
import type { CapsuleEndpoint, ResolvedCapsule } from "./resolve.js";

type Obj = Record<string, unknown>;

function get(capsule: Obj, key: string): unknown {
  return capsule[key];
}

function frame(capsule: Obj): Obj {
  return {
    schema_version: get(capsule, "schema_version"),
    capsule_id: get(capsule, "capsule_id"),
    content_is_untrusted: get(capsule, "content_is_untrusted"),
  };
}

/** Which carried sub-object each section endpoint projects. */
const SECTION_KEY: Record<"slice" | "context" | "trail" | "repo" | "environment", string> = {
  slice: "slice",
  context: "context_resume_packet",
  trail: "trail_anchors",
  repo: "repo_pin",
  environment: "environment",
};

export function summaryPayload(resolved: ResolvedCapsule, viewerUrl: string, skillSha: string): Obj {
  const c = resolved.capsule;
  const renderState = (get(c, "render_state") as Obj | undefined) ?? {};
  const test = get(c, "test") as Obj | null;
  const intent = (get(c, "intent") as Obj | undefined) ?? {};
  const summary = (get(c, "summary") as Obj | undefined) ?? {};
  return {
    _discovery: discoveryBlock(viewerUrl, skillSha),
    schema_version: get(c, "schema_version"),
    capsule_id: get(c, "capsule_id"),
    content_is_untrusted: get(c, "content_is_untrusted"),
    intent_headline: get(intent, "headline") ?? null,
    // Carried verbatim (the frozen `failing_step`; alias `focus_step`).
    failing_step: get(c, "failing_step") ?? null,
    // Rule 5: the recorded verdict is a CLAIM, not a re-run. The frozen v1
    // envelope carries render_state only (no oracle verdict enum), so we
    // surface exactly that and flag it — never inventing a "fixed"/"reproduces".
    recorded_verdict: {
      claim_not_proof: true,
      render_state_replay: get(renderState, "replay") ?? null,
      render_state_closure: get(renderState, "closure") ?? null,
      oracle: test ?? null,
    },
    outcome_taxonomy: get(summary, "outcome_taxonomy") ?? null,
    how_to_go_deeper: `${viewerUrl}/index`,
  };
}

function sectionBytes(value: unknown): number {
  // canonicalStringify output is pure ASCII (ensure_ascii), so char length ==
  // UTF-8 byte length.
  return canonicalStringify(value).length;
}

export function indexPayload(resolved: ResolvedCapsule): Obj {
  const c = resolved.capsule;
  const redaction = (get(c, "redaction") as Obj | undefined) ?? {};
  const manifest = (get(redaction, "manifest") as Obj | undefined) ?? {};
  // Counts-only redaction summary (rule 2: never any matched text).
  const manifestSummary: Obj = {
    schema_version: get(manifest, "schema_version") ?? null,
    floor_satisfied: get(manifest, "floor_satisfied") ?? null,
    redactions_applied: get(manifest, "redactions_applied") ?? null,
    findings_total: get(manifest, "findings_total") ?? null,
    fields_excluded: get(manifest, "fields_excluded") ?? null,
    home_paths_scrubbed: get(manifest, "home_paths_scrubbed") ?? null,
  };
  return {
    ...frame(c),
    embedded: get(c, "embedded") ?? null,
    sections: [
      { endpoint: "/summary", bytes: sectionBytes({ intent: get(c, "intent"), failing_step: get(c, "failing_step"), summary: get(c, "summary") }), keys: ["intent", "failing_step", "summary"] },
      { endpoint: "/slice", bytes: sectionBytes(get(c, "slice")), keys: ["slice"], embedded_schema: "opentraces.trace_slice.v1" },
      { endpoint: "/context", bytes: sectionBytes(get(c, "context_resume_packet")), keys: ["context_resume_packet"], embedded_schema: "opentraces.context_resume.v1" },
      { endpoint: "/trail", bytes: sectionBytes(get(c, "trail_anchors")), keys: ["trail_anchors"] },
      { endpoint: "/repo", bytes: sectionBytes(get(c, "repo_pin")), keys: ["repo_pin"] },
      { endpoint: "/environment", bytes: sectionBytes(get(c, "environment")), keys: ["environment"] },
      { endpoint: "/full", bytes: resolved.raw.length, keys: ["<entire opentraces.capsule.v1 envelope>"] },
    ],
    // Rule 4: limitations are the contract — surfaced verbatim.
    limitations: get(c, "limitations") ?? [],
    // Rule 3: privacy is author intent — carried, not re-derived.
    privacy_scope: get(c, "privacy_scope") ?? null,
    redaction: { manifest: manifestSummary },
  };
}

/** Payload for a named section endpoint (`slice`/`context`/`trail`/`repo`/`environment`). */
export function sectionPayload(
  resolved: ResolvedCapsule,
  section: "slice" | "context" | "trail" | "repo" | "environment",
): Obj {
  const c = resolved.capsule;
  const key = SECTION_KEY[section];
  return {
    ...frame(c),
    [key]: get(c, key) ?? null,
  };
}

/**
 * True for endpoints served as JSON. `full` is JSON but served as raw
 * pass-through bytes; `skill` is served as markdown text.
 */
export function isJsonSection(endpoint: CapsuleEndpoint): endpoint is "slice" | "context" | "trail" | "repo" | "environment" {
  return endpoint === "slice" || endpoint === "context" || endpoint === "trail" || endpoint === "repo" || endpoint === "environment";
}
