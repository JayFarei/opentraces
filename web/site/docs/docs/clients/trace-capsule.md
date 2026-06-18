# Trace Capsule

A **Trace Capsule** is a privacy-bounded record of how one agent used one consumed
product: an "Agent Experience Report". It packages a redacted, self-contained slice
of a captured session so a maintainer agent or teammate can resolve the episode with
a single command.

A runnable repro test is **optional evidence, not the point**. Capsules with
`test=null` are fully first-class: every verb (`export`, `preview`, `open`,
`share`, `issue`) works identically whether or not the session had a failing command.

The underlying object stays `opentraces.capsule.v1` throughout. The presentation
reframe (command noun, `<!-- opentraces-capsule: <id> -->` wire markers, issue
idempotency) is unchanged.

## What a Capsule Carries

A capsule assembles four signals that make an episode replayable by intent, not
by bytes:

| Signal | Capsule field |
|--------|---------------|
| What the agent tried to do | `intent` (deterministic `Burst.intent`) |
| What it knew and saw | `context_resume_packet` (4 inlined layers) |
| Where it got stuck or finished | `failing_step` + bounded `slice` |
| The repo state it ran against | `repo_pin` (remote + sha-pinned commit + changed files) |

Optional additive keys (null-tolerant, absent on older capsules without error):

- `product` — the consumed product/dependency this episode is bound to (grouping anchor + product-episode slice)
- `summary.outcome_taxonomy` — derived outcome: `completed` / `abandoned` / `unclear`
- `privacy_scope` — structural egress declaration (bools/ints only; never a classifier verdict)

## Verbs

### `capsule export` — build a local capsule

```bash
opentraces capsule export <trace-id> \
  [--project <repo>] \
  [--product <name>] \
  [--include-prompts] \
  [--test-command "pytest tests/test_foo.py -k failing"] \
  [--expect-error "AssertionError"] \
  [--setup-command "pip install -e ."] \
  [--consume package:humanduration=git+https://github.com/o/r@v0.1.0] \
  [--from-session <id> [--from-agent claude|codex]] \
  [--product-full-span] \
  [--progress auto|plain|json|never] \
  [--out <dir>] \
  [--bundle] \
  [--json]
```

Writes `capsules/v1/<id>/capsule.json` + `capsule.md` under `<project>/.opentraces/`.
Primary stdout is the `capsule.json` path. `--json` prints the full envelope.
The `<trace-id>` is optional when `--from-session` is given (the two are mutually exclusive).

- `--product <name>` binds the capsule to one consumed product and scopes the slice
  to steps that reference it. If omitted and the session captured dependency names,
  the CLI emits `--consume` hints to stderr (never auto-writes them).
- `--include-prompts` opts prompt-bearing fields (system prompt + per-step reasoning)
  IN; they are excluded by default (see [Layered Redaction](#layered-redaction)).
- `--bundle` embeds a hermetic `git archive` at the pinned commit so the test runs
  even if the commit is later unreachable.
- `--from-session <id>` builds a capsule for the **current turn** from a live hook
  sidecar instead of a retained trace id — it ingests the session first, then exports.
  `--from-agent claude|codex` selects which sidecar; if the session isn't found or
  isn't parseable the command prints a specific remediation (and Codex sidecars whose
  rollout content isn't yet at the ingested path fall back to that remediation).
- `--product-full-span` opts the `--product` slice out of the default radius clamp
  (the bounded window that keeps a large session from slicing end-to-end). Use it
  only when you need the full matched span.
- `--progress auto|plain|json|never` reports stage/heartbeat to **stderr** during the
  slow export phases (slice / context / trail-anchor projection); the `--json` envelope
  on stdout stays clean. `auto` is quiet on a non-TTY. (Shared with the `trace index
  rebuild` progress contract.)

### `capsule preview` — inspect egress before anything leaves

```bash
opentraces capsule preview <trace-id> \
  [--project <repo>] \
  [--product <name>] \
  [--include-prompts] \
  [--json]
```

Runs the full redaction pipeline, then prints:

- redaction manifest by field path
- `business_logic` findings (internal hostnames, collab-tool URLs, DB strings, AWS account ids)
- the `privacy_scope` block
- the destinations a publish WOULD reach

Writes and publishes **nothing**. This is the developer-approval checkpoint.

### `capsule open` — the consume verb

```bash
opentraces capsule open <ref> [--json] [--summary]
```

Resolves a capsule from a file path, `https://` URL, or `hf://` ref. `--json` (the
default) prints the frozen `opentraces.capsule.v1` envelope so a maintainer agent can
parse it with zero bespoke code. `--summary` prints the human markdown instead.

The `--json` flag is the default and is accepted explicitly so the command embedded
in the issue body runs verbatim.

### `capsule share` — mint and optionally publish the URL

```bash
opentraces capsule share <trace-id> \
  [--repo <hf-owner/name>] \
  [--publish] [--private] \
  [--product <name>] [--include-prompts] \
  [--bundle] [--copy] [--yes]
```

Without `--publish`: mints the shareable URL locally. Primary stdout is the URL.

With `--publish`: uploads `capsule.json` + `capsule.md` (and the bundle if `--bundle`)
to the HuggingFace dataset repo and pins the URL to the immutable publish commit sha.
Before uploading, the consent gate runs (see [Consent Gate](#consent-gate)).

Default repo is `<you>/opentraces-capsules` (or `cfg.capsule_repo` if set). `--copy`
copies the URL to the clipboard.

### `capsule issue` — render or file the GitHub issue

```bash
# Dry run: print the rendered issue body.
opentraces capsule issue <trace-id> [--repo <hf-owner/name>] [--issue-repo <gh-owner/name>]

# Publish to HF and file or update the issue.
opentraces capsule issue <trace-id> --publish [--yes]
```

The issue repo is inferred from the capsule's repo pin (the repo the session ran
against) so the common case is just:

```bash
opentraces capsule issue <trace-id> --publish
```

`issue create` is idempotent: it searches the GitHub repo for the hidden
`<!-- opentraces-capsule: <id> -->` marker and updates rather than duplicates.
The consent gate runs before any egress (see [Consent Gate](#consent-gate)).

### `capsule replay`, `capsule test`, `capsule verdict`, `capsule watch`

```bash
# Re-pose the intent as a structured packet for a maintainer agent.
opentraces capsule replay <ref> [--against HEAD] [--json]

# Run the captured repro command as an executable test (requires test.command).
opentraces capsule test <ref> [--against HEAD] [--from-bundle] \
  [--with name=ver] [--matrix name=v1,v2,v3] [--verdict-to issue] [--json]

# Post a manual verdict to the issue.
opentraces capsule verdict <issue-ref> --state fixed|reproduces|inconclusive \
  [--note "..."] [--close]

# Poll an issue for resolution (unblock cue).
opentraces capsule watch <issue-ref> [--timeout 300] [--json]
```

`capsule test` exits early with a clear message if the capsule carries no test
command; use `capsule replay` for intent-only sessions. `--matrix name=v1,v2`
sweeps one consumed dependency and reports which version flips the verdict to `fixed`
(`resolved_in`).

## The 3-Way Render Banner

The presentation banner on the GitHub issue and `capsule.md` follows a 3-way logic
that reflects the episode type. The underlying `opentraces.capsule.v1` object and
all wire markers are unchanged:

| Condition | Banner |
|-----------|--------|
| `is_failure=true` and `test.command` present | "Agent Support Packet" |
| `is_failure=true` and `test=null` | "Agent Experience Report (blocked episode)" |
| `is_failure=false` | "Agent Experience Report (usage episode)" |

## Layered Redaction

Every capsule runs a mandatory redaction floor before any bytes leave the machine.
The floor is unconditional regardless of project config:

```
floor = ("regex", "entropy", "business_logic")
```

The `business_logic` detector (added in `SECURITY_VERSION 0.6.0`) redacts internal
hostnames, collab-tool URLs, DB connection strings, and AWS account ids as spans.

On top of the detector floor, a `capsule_scope` field-path exclusion zeroes
prompt-bearing fields to `[EXCLUDED:<path>]` markers before any detector even runs.
This is the only true "this never leaves" guarantee. By default these fields are
excluded:

- `context_resume_packet.messages` (per-step reasoning + tool calls)
- `context_resume_packet.system` (system prompt)

Pass `--include-prompts` to opt them in. Either way, the redaction manifest records
what happened via counts only (`fields_excluded` / `excluded_field_paths`) without
serializing the excluded content.

Additional safety properties:

- **Counts-only manifest.** `redaction.manifest` aggregates counts by tool, severity,
  and field path. It never serializes `Finding.matched_text` (the literal secret value).
- **Home-path scrub.** `/Users/<name>/` and bare username tokens are replaced with
  `~` and `<user>` respectively.
- **Untrusted content.** `content_is_untrusted: true` on the envelope. The human render
  routes captured text through a sentinel/fence-breakout/heading-injection stripper.

The gate asserts the floor RAN (not that zero redactions were applied): a clean
session with nothing to redact must be distinguishable from a session where the floor
did not run.

## The `privacy_scope` Block

`privacy_scope` is a structural egress declaration emitted alongside the redaction
manifest. It contains only bools and ints, never a classifier verdict, so it is safe
to read in automated pipelines:

```json
{
  "prompts_included": false,
  "fields_excluded": 2,
  "redaction_floor": ["regex", "entropy", "business_logic"],
  "floor_satisfied": true
}
```

## Consent Gate

Both `share --publish` and `issue --publish` share the same egress confirmation
before any bytes reach a public destination. The gate names the specific destinations
(HF dataset repo, GitHub repo) and summarizes the redaction:

```
This will PUBLISH a redacted capsule to: HF dataset (public): owner/opentraces-capsules; GitHub issue: owner/repo.
  redaction floor ['regex', 'entropy', 'business_logic'] ran · 3 redactions · 1 business-logic findings · 2 prompt fields excluded.
Proceed? [y/N]
```

Pass `--yes` to bypass for scripts and agents.

## URL Design

The shareable URL points at one self-contained file, pinned to the immutable publish
commit sha:

```
https://huggingface.co/datasets/<owner>/<repo>/resolve/<commit-sha>/capsules/v1/<id>/capsule.json
```

`publish_capsule` uploads only `capsule.json` + `capsule.md` (not the whole private
bucket). Pinning to the commit sha means the URL is immutable: the file it resolves
to can never silently change.

## Self-Sufficiency

`export` sources the context resume packet from the live Context Tree projection when
present, and falls back to the trace's own bucket companion
(`bucket/contexts/v1/<slug>/<trace>/nodes.jsonl` + layer blobs). A capsule resolves
with zero access to the originating machine. Degraded captures produce a valid
`closure_intent_only` capsule (recorded in `limitations`), never a crash.

## Honest Capture Gaps

The following are gaps in v1 capture, not limitations of the capsule format:

- **URL/docs consultation is not captured.** The parse layer handles `Read`, `Edit`,
  `Write`, and `Grep` tool calls. `WebFetch` is absent; URL documentation consulted
  during a session does not appear in the capsule. Local-file docs are derivable;
  remote docs are a v1 capture gap.
- **Runtime dependency versions are not captured.** `TraceRecord.dependencies`
  carries names only (no version pins). `suggest_consumes` therefore emits
  `--consume package:<name>=` hints to stderr and never auto-populates
  `environment.consumes` or `product`. Confirm the version before passing `--consume`.
- **Product-to-step binding is heuristic.** There is no per-step product label in
  the captured data. The `product_episode` slice matches the product string against
  tool-call and observation text. Capsules carry a `product_inferred_not_captured`
  limitation when `--product` is used.

## End-to-End Example

```bash
# 1. Find the trace you want to package.
opentraces trace query --since 7d --cwd --lex "humanduration parsing"

# 2. Preview what would leave the machine (writes/publishes nothing).
opentraces capsule preview <trace-id> \
  --product humanduration \
  --test-command "pytest tests/test_parse.py -k parse_iso"

# 3. Export a local capsule.
opentraces capsule export <trace-id> \
  --product humanduration \
  --test-command "pytest tests/test_parse.py -k parse_iso" \
  --setup-command "pip install -e ." \
  --consume "package:humanduration=git+https://github.com/owner/humanduration@main"

# 4. Share it (publishes to HF, shows consent gate).
opentraces capsule share <trace-id> --publish

# 5. File a GitHub issue embedding the URL (consent gate runs again, once total).
opentraces capsule issue <trace-id> --publish --yes

# 6. A maintainer agent resolves it.
opentraces capsule open https://huggingface.co/.../capsule.json --json
```

## Accepted Command Options Reference

All options below apply to `export`, `share`, `issue`, and `preview` unless noted:

| Option | Default | Description |
|--------|---------|-------------|
| `--project <dir>` | CWD | Project directory |
| `--product <name>` | none | Bind to one consumed product (grouping + slice scope) |
| `--include-prompts` | off | Include system prompt + per-step reasoning (excluded by default) |
| `--step <n>` | inferred | Failing/anchor step index |
| `--node <id>` | from step | Context node id |
| `--radius <n>` | 4 | Slice radius around the anchor step |
| `--repo-url <url>` | from git | Override the public remote URL in the repo pin |
| `--test-command <cmd>` | none | Declare a runnable repro command |
| `--expect-error <str>` | none | Expected error string (omit = expect non-zero exit) |
| `--setup-command <cmd>` | none | Setup/install step before the repro |
| `--consume <spec>` | none | Record a consumed dependency (`[package\|service:]NAME=PIN\|URL`) |
| `--bundle` | off | Embed a hermetic `git archive` source bundle |
| `--yes` | off | Skip the consent gate (share/issue only, for scripts/agents) |
| `--json` | off | Emit JSON to stdout |
