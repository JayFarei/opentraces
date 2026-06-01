# Trace Capsule (v1 prototype, plan 082)

Agent-to-agent bug reporting. A coding agent hits a bug in an upstream OSS
project; opentraces packages the failing session into a portable, redacted,
**agent-consumable** capsule and attaches a shareable URL to a GitHub issue. A
maintainer's agent resolves the URL with one command and re-poses the captured
*intent* against current code.

This is the **share-first** v1 (autoreview-resolved). The replay loop is the
maintainer's own agent, driven via the `capsule open` consume verb. There is no
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
# Build a local, redacted, self-sufficient capsule (zero remote config).
opentraces capsule export <trace-id> --project <repo>
#   -> writes capsules/v1/<id>/capsule.json + capsule.md, prints the json path.

# The CONSUME verb: resolve from a file / https / hf:// ref, print the envelope.
opentraces capsule open <ref> --json        # zero bespoke parsing for the agent
opentraces capsule open <ref> --summary     # human markdown

# Mint the shareable URL (and optionally publish it).
opentraces capsule share <trace-id> --repo <hf-owner/name> [--copy]
opentraces capsule share <trace-id> --repo <hf-owner/name> --execute --public

# Render / file the GitHub issue (embeds the URL + the `capsule open` command).
opentraces capsule issue render <trace-id> --repo <hf-owner/name>
opentraces capsule issue create <trace-id> --issue-repo <gh-owner/name> \
  --repo <hf-owner/name> --execute --public [--copy]
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
- **Explicit public consent.** `--execute` to a public destination requires
  `--public` naming the repo.

## URL design

The shareable URL points at ONE self-contained file, never a bucket tree:

```
https://huggingface.co/datasets/<owner>/<repo>/resolve/<commit-sha>/capsules/v1/<id>/capsule.json
```

`publish_capsule` uploads ONLY `capsule.json` + `capsule.md` (it does NOT reuse the
whole-bucket private `bucket_remote.remote_push`) and pins the URL to the publish
commit oid for immutability. A `capsule.md` mirror renders as a human page on HF.

## Self-sufficiency

`export` sources the resume packet from the live Context Tree projection when
present, else from the trace's OWN bucket companion
(`bucket/contexts/v1/<slug>/<trace>/nodes.jsonl` + layer blobs). The capsule
resolves with zero access to the originating machine. Degraded captures produce a
valid `closure_intent_only` capsule (recorded in `limitations`), never a crash.

## Usage episode (plan 090) — privacy-bounded "Agent Experience Report"

A capsule is now framed as a **privacy-bounded usage episode**: the asset is how an
agent used ONE consumed product, with a runnable test as **optional evidence, not the
point**. `test=null` is first-class — every path (export / preview / open / render /
publish) works with no test. The change is additive: `REQUIRED_KEYS` and
`CAPSULE_SCHEMA_VERSION` are unchanged; `SECURITY_VERSION` bumped to `0.6.0`.

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

## Module map

| File | Responsibility |
|------|----------------|
| `contract.py` | Frozen `opentraces.capsule.v1` envelope, `capsule_id`, validate (+reject-newer). |
| `export.py` | `export_capsule(project_dir, trace_id, ...)` — assembly over slice + resume packet + intent + repo pin. |
| `bucket_context.py` | Self-sufficient resume packet from the trace's bucket companion. |
| `redaction.py` | Mandatory floor + counts-only manifest + hard gate + home scrub. |
| `render.py` | `render_issue_body` / `render_capsule_markdown` (agent-first, human-second). |
| `share.py` | URL mint, local write, HF publish (capsule-only), clipboard, idempotent `gh` issue. |
| `../../cli/capsule.py` | `opentraces capsule {export, open, share, issue}`. |

Tests: `tests/test_capsule.py` (hermetic) + `tests/test_capsule_export_integration.py`.
