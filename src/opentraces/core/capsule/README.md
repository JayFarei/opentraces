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
