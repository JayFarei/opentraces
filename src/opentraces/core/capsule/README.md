# Trace Capsule (v1 prototype, plan 082)

Agent-to-agent bug reporting. A coding agent hits a bug in an upstream OSS
project; opentraces packages the failing session into a portable, redacted,
**agent-consumable** capsule and attaches a shareable URL to a GitHub issue. A
maintainer's agent resolves the URL with one command and re-poses the captured
*intent* against current code.

This is the **share-first** v1 (autoreview-resolved). The replay loop is the
maintainer's own agent, driven via the `capsule get` consume verb (the pre-v7
`capsule open` spelling stays callable, hidden from `--help`). There is no
opentraces-side agent runner in v1.

## The four signals (from the blog)

A capsule carries what makes a bug reproducible by intent, not by bytes:

| Signal | Capsule field |
|--------|---------------|
| what the agent tried to do (`action_trajectory`) | `intent` (deterministic `Burst.intent`) |
| what it knew and saw (`ctx_tree`) | `context_resume_packet` (4 inlined layers) |
| where it failed (`trace_step`) | `failing_step` + bounded `slice` |
| the repo state it failed against (`git snapshot`) | `repo_pin` (remote + sha + changed files) |

## Verbs

```bash
# Seal a local, redacted, self-sufficient capsule (zero remote config).
# REF is a v7 address (<trace>, <trace>:<step>, <trace>:A-B); --from-step/--to-step
# is the explicit span seam. The pre-v7 `capsule export` spelling (richer flag
# set: --test-command/--setup-command/--consume/--from-session) stays callable.
opentraces capsule create <ref> --project <repo>
#   -> writes capsules/v1/<id>/capsule.json + capsule.md, prints the json path.

# The read-only CONSUME verb: resolve from a file / https / hf:// ref, print the envelope.
opentraces capsule get <ref> --json        # zero bespoke parsing for the agent
opentraces capsule get <ref> --summary     # human markdown

# The explicit opt-in WRITE verb: resolve + materialize into the local bucket
# as a first-class trace (idempotent on the same capsule id).
opentraces capsule import <ref> --json

# Mint the shareable URL (and optionally publish it).
opentraces capsule share <trace-id> --repo <hf-owner/name> [--copy]
opentraces capsule share <trace-id> --repo <hf-owner/name> --publish

# Render / file the GitHub issue (embeds the URL + the `capsule get` command).
opentraces capsule issue <trace-id> --repo <hf-owner/name>
opentraces capsule issue <trace-id> --issue-repo <gh-owner/name> \
  --repo <hf-owner/name> --publish [--copy]
```

## Safety (the moat)

- **Mandatory redaction floor.** `regex` + `entropy` detectors run over the WHOLE
  assembled envelope (including the inlined resume packet, which comes from the
  event log, not the record), regardless of project config. The gate (`exit !=0`)
  fires on "the floor ran", not on `redactions_applied > 0`.
- **Counts-only manifest.** `redaction.manifest` aggregates `by_tool` /
  `by_severity` / `by_field_path` counts. It NEVER serializes `Finding.matched_text`
  (which holds the literal secret). Asserted by a sentinel unit test.
- **Home-path scrub.** Absolute `/Users|/home/<user>/...` paths are scrubbed to `~`.
- **Untrusted content.** `content_is_untrusted: true` on the envelope; the human
  render routes captured text through `branch_context.redact_intent` (strips
  adversarial sentinels / fence-breakouts / heading injection).
- **Explicit public consent.** `--publish` to a real destination requires a
  named `--repo`/`--issue-repo` (or an inferred one) and runs a consent-gate
  confirmation before any bytes leave, unless `--yes`; `--private` opts the HF
  dataset repo out of public visibility.
- **Bundle secret-scan gate (M3).** A `--bundle`'d capsule's exact shipped
  archive bytes are scanned for secrets before `share --publish`/`issue
  --publish`; a finding blocks the publish (zero bytes out) unless
  `--i-accept-bundle-findings`. This is a publish GATE, never a trust factor.

## URL design

The shareable URL points at ONE self-contained file, never a bucket tree:

```
https://huggingface.co/datasets/<owner>/<repo>/resolve/<commit-sha>/capsules/v1/<id>/capsule.json
```

`publish_capsule` uploads ONLY `capsule.json` + `capsule.md` (it does NOT reuse the
whole-bucket private `bucket_remote.remote_push`) and pins the URL to the publish
commit oid for immutability. A `capsule.md` mirror renders as a human page on HF.

## Self-sufficiency

Sealing (`create` or `export`) sources the resume packet from the live Context
Tree projection when present, else from the trace's OWN bucket companion
(`bucket/contexts/v1/<slug>/<trace>/nodes.jsonl` + layer blobs). The capsule
resolves with zero access to the originating machine. Degraded captures produce a
valid `closure_intent_only` capsule (recorded in `limitations`), never a crash.

## Usage episode (plan 090) — privacy-bounded "Agent Experience Report"

A capsule is now framed as a **privacy-bounded usage episode**: the asset is how an
agent used ONE consumed product, with a runnable test as **optional evidence, not the
point**. `test=null` is first-class — every path (create/export / preview / get/open /
import / render / publish) works with no test. The change was additive:
`REQUIRED_KEYS` and `CAPSULE_SCHEMA_VERSION` stayed unchanged; `SECURITY_VERSION`
was bumped to `0.6.0` at the time (current: `0.8.0`, issue #143).

```bash
# Inspect egress BEFORE anything leaves the machine (writes/publishes NOTHING):
opentraces capsule preview <trace-id> --project <repo> --product <name> [--json]
#   -> redaction by field-path · business_logic findings · privacy_scope · destinations

# Export/share/issue accept the new options:
#   --product <name>     bind to one consumed product (grouping anchor + product-episode slice)
#   --include-prompts    OPT IN to prompt-bearing fields (system prompt + per-step reasoning),
#                        which are EXCLUDED by default.
```

Additive envelope keys (all null-tolerant, absent on older capsules without error):
`product` (grouping anchor), `summary.outcome_taxonomy`
(`completed`/`abandoned`/`unclear` derived; `workaround_found`/`blocked_by_*` reserved),
and a structural `privacy_scope` (bools/ints only — never a classifier verdict).

Layered redaction: the floor is now `("regex", "entropy", "business_logic")` — the
`business_logic` Detector redacts internal hostnames, collab-tool URLs, DB connection
strings, and AWS account ids as **spans**. The `capsule_scope` Transformer applies
**field-path exclusion** (the only true "this never leaves" guarantee); excluded paths
are recorded as counts in the manifest (`fields_excluded` / `excluded_field_paths`).

The naming reframe is **presentation only**: the render banner is 3-way (Agent Support
Packet when a failure has a test / Agent Experience Report blocked-episode / usage-episode),
but the `capsule` command noun and the `<!-- opentraces-capsule: <id> -->` /
`opentraces-capsule-verdict:` wire markers are unchanged (issue idempotency preserved).

### Honest capture gaps (do not imply these exist)

- **URL docs consulted are NOT captured.** `_extract_snippets` (`capture/claude_code/parse.py`)
  handles `Read`/`Edit`/`Write`/`Grep` only — `WebFetch` is absent. Local-file docs are
  derivable; URL/docs consultation is a capture gap, not a projection.
- **Runtime-resolved dependency versions are NOT captured.** `TraceRecord.dependencies`
  is name-only (no pins). `suggest_consumes` therefore emits stderr `package:<name>=`
  hints only and never auto-populates `environment.consumes` / `product`.
- **Product→step binding is heuristic.** There is no captured per-step product label;
  the `product_episode` slice matches the product string against tool-call/observation
  text, so capsules carry a `product_inferred_not_captured` limitation.

## Current-turn capture & bounded slow paths (issue #98)

**Capsule vs the raw hook sidecar — which to attach to a bug report.** A raw
agent hook sidecar (Codex's `.opentraces/codex-cli/hooks/<id>.jsonl`, or a
Claude session transcript) is the *unredacted* on-disk capture artifact: it is
host-coupled, carries absolute home paths, and has had NO security floor or
prompt exclusion applied. Never attach it to a public bug report. A **capsule**
is the privacy-bounded, frozen, self-contained projection of that same session —
redacted through the mandatory floor, prompt-bearing fields excluded by default,
bounded by construction. For bug reports, always export a capsule.

**Current turn (`capsule export --from-session <id>`).** To capsule the session
you are *in* (before the Stop hook finalizes it), pass `--from-session <id>`
instead of a trace id (the two are mutually exclusive). The resolver
(`from_session.py`) materializes the live session into the bucket via the shared
idempotent `ingest_one_session` choke point (so it is redacted identically to a
finalized trace), then the normal export runs. Sources resolve per-agent — Codex
→ its opentraces hook sidecar; Claude → its `~/.claude/projects/*/<id>.jsonl`
transcript. A miss emits one of three cause-specific remediations (not-found /
excluded-project / lock-contention). NOTE: a pure Codex hook sidecar carries no
turns, so the Codex produces-a-capsule path requires rollout content at that path
(Codex=PARTIAL); the Claude transcript path is the load-bearing current-turn case.

**Bounded `--product` + progress.** `--product` slices were previously unbounded
(`min..max` over every step that references the product), which could span almost
a whole session and hang. The episode is now capped at `2*radius` around the first
match by default (with a deterministic `product_episode_bounded` limitation +
`slice.metadata.product_match_span` / `bounded_to`); `--product-full-span` opts
back into the historical unbounded span. `capsule export` / `capsule preview` also
accept `--progress auto|plain|json|never` (the shared issue-#88 contract) so the
two projection scans (trail anchors, context resume) are observable, never
silently hung; `preview --json` carries an additive `telemetry.stages` block.
There is NO hard wall-clock `--deadline` in v1 (deferred to v1.1) — a clean
partial over a half-scanned Trail projection is not well-defined.

## Module map

| File | Responsibility |
|------|----------------|
| `contract.py` | Frozen `opentraces.capsule.v1` envelope, `capsule_id`, validate (+reject-newer). |
| `export.py` | `export_capsule(project_dir, trace_id, ...)` — assembly over slice + resume packet + intent + repo pin; optional `progress` reporter + bounded `--product` slice (#98). |
| `from_session.py` | `resolve_session_to_trace(session_id, project_dir, agent)` — materialize a CURRENT turn into the bucket (Codex sidecar / Claude transcript) for `--from-session` (#98). |
| `bucket_context.py` | Self-sufficient resume packet from the trace's bucket companion. |
| `redaction.py` | Mandatory floor + counts-only manifest + hard gate + home scrub. |
| `render.py` | `render_issue_body` / `render_capsule_markdown` (agent-first, human-second). |
| `share.py` | URL mint, local write, HF publish (capsule-only), clipboard, idempotent `gh` issue. |
| `../../cli/capsule.py` | `opentraces capsule {create, get, import, preview, share, issue, replay, test, verdict, watch}` (`export`/`open` are the pre-v7 spellings, hidden but callable; `export` gains `--from-session` / `--from-agent` / `--product-full-span` / `--progress`; `preview` gains `--product-full-span` / `--progress`). |
| `replay.py` | Lattice clamp (`clamp(oracle_trust, env_tier, diff_trust, sandbox_tier) -> verdict_trust`) + the four-property replay-honesty surface (M3, ADR-0008). |
| `run.py` | `capsule test`'s isolated repro runner (`core/isolation.py`); stamps the honest `sandbox_tier`. |

Tests: `tests/test_capsule.py` (hermetic) + `tests/test_capsule_export_integration.py`.
