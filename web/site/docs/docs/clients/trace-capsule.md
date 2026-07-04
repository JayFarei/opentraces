# Trace Capsule

A **Trace Capsule** is a privacy-bounded record of how one agent used one consumed
product: an "Agent Experience Report". It packages a redacted, self-contained slice
of a captured session so a maintainer agent or teammate can resolve the episode with
a single command.

A runnable repro test is **optional evidence, not the point**. Capsules with
`test=null` are fully first-class: every verb (`create`, `preview`, `get`,
`import`, `share`, `issue`) works identically whether or not the session had a failing command.

The seal/consume/write verbs are `capsule create` (seal), `capsule get`
(read-only resolve), and `capsule import` (the explicit opt-in write into the
local bucket). The pre-v7 spellings `capsule export` and `capsule open` still
work (hidden from `--help`, kept callable so existing issue-embedded commands
and scripts never break) — this doc uses the current names throughout.

The underlying object stays `opentraces.capsule.v1` throughout. The presentation
reframe (command noun, `<!-- opentraces-capsule: <id> -->` wire markers, issue
idempotency) is unchanged.

Per the [Seal Family contract](https://github.com/JayFarei/opentraces/blob/main/docs/adr/0008-seal-family-contract.md) (ADR-0008),
a capsule is one of exactly two things that "seal": an **immutable, URL-addressed
seal** over one scope, byte-stable under re-seal, with a deterministic
`capsule_id`. (The other is a [dataset](../workflow/datasets.md), a growing,
reviewed seal of workflow-projected rows.) Everything else that carries capsule
content, a rendered issue body, the capsule-worker HTML page, is a *rendering* of
that seal, not a seal itself.

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

### `capsule create` — seal a local capsule

```bash
opentraces capsule create <ref> \
  [--from-step <n> --to-step <n>] \
  [--project <repo>] \
  [--product <name>] \
  [--include-prompts] \
  [--repo-url <url>] \
  [--progress auto|plain|json|never] \
  [--out <dir>] \
  [--bundle] \
  [--json]
```

`<ref>` is a v7 address: a whole `<trace>`, a point `<trace>:<step>`, or a span
`<trace>:A-B`. The address selects the scope directly — no step/radius flag
soup; `--from-step`/`--to-step` is the equivalent explicit span seam. Writes
`capsules/v1/<id>/capsule.json` + `capsule.md` under `<project>/.opentraces/`.
Primary stdout is the `capsule.json` path. `--json` prints the full envelope.

- `--product <name>` binds the capsule to one consumed product and scopes the slice
  to steps that reference it.
- `--include-prompts` opts prompt-bearing fields (system prompt + per-step reasoning)
  IN; they are excluded by default (see [Layered Redaction](#layered-redaction)).
- `--bundle` embeds a hermetic `git archive` at the pinned commit so a later
  `capsule test --from-bundle` runs even if the commit is later unreachable;
  the shipped bundle bytes are secret-scanned before `share --publish` (see
  [Bundle Safety and Sandbox](#bundle-safety-and-sandbox)).
- `--progress auto|plain|json|never` reports stage/heartbeat to **stderr** during the
  slow seal phases (slice / context / trail-anchor projection); the `--json` envelope
  on stdout stays clean. `auto` is quiet on a non-TTY.

`create` is the v7-address seal path; it does not (yet) declare a runnable test
command, an explicit `--consume` spec, or seal from a live in-flight session.
For those, the pre-v7 `capsule export` spelling stays callable (hidden from
`--help`) with its full original flag set: `--test-command`, `--expect-error`,
`--setup-command`, `--consume`, `--from-session [--from-agent claude|codex]`,
and `--product-full-span`. Both verbs write the same `opentraces.capsule.v1`
object; `export` is the richer flag surface, `create` is the v7-address one.

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

### `capsule get` — the read-only consume verb

```bash
opentraces capsule get <ref> [--json/--no-json] [--summary]
```

Resolves a capsule from a file path, `https://` URL, or `hf://` ref. `--json` (the
default) prints the frozen `opentraces.capsule.v1` envelope so a maintainer agent can
parse it with zero bespoke code. `--summary` prints the human markdown instead.
Read-only: no `~/.opentraces`, bucket, or project state is created, so a
maintainer in a brand-new environment can `get` a capsule and read it.

The `--json` flag is the default and is accepted explicitly so the command embedded
in the issue body runs verbatim.

### `capsule import` — the explicit opt-in write

```bash
opentraces capsule import <ref> [--source-layer <label>] [--json]
```

Resolves the same way as `get`, but WRITES the result into the local bucket as
a first-class trace: the carried spine is materialized into a schema-valid
`TraceRecord` under the reused trace id, and its recorded anchors into the
per-trace Trail companion, so the imported capsule projects natively through
`trace map` / `trace slice` / `trace get`. Same capsule id over an existing
trace is an idempotent no-op; a different capsule id over the same trace
scope-merges. `--source-layer` (default `capsule_import`) labels the
provenance recorded on the imported record.

### `capsule share` — mint and optionally publish the URL

```bash
opentraces capsule share <trace-id> \
  [--repo <hf-owner/name>] \
  [--publish] [--private] \
  [--product <name>] [--include-prompts] \
  [--bundle] [--copy] [--yes] \
  [--i-accept-bundle-findings]
```

Without `--publish`: mints the shareable URL locally. Primary stdout is the URL.

With `--publish`: uploads `capsule.json` + `capsule.md` (and the bundle if `--bundle`)
to the HuggingFace dataset repo and pins the URL to the immutable publish commit sha.
Before uploading, the consent gate runs (see [Consent Gate](#consent-gate)) and, if
`--bundle` was used, the shipped bundle bytes are secret-scanned (see
[Bundle Safety and Sandbox](#bundle-safety-and-sandbox)); a finding blocks the
publish (zero bytes out) unless `--i-accept-bundle-findings`.

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
opentraces capsule test <ref> [--against HEAD] [--repo-dir <dir>] [--from-bundle] \
  [--inherit-env] [--timeout 180] [--yes] \
  [--unsafe-run-on-host] [--i-own-isolation] \
  [--with name=ver] [--matrix name=v1,v2,v3] [--verdict-to issue] [--close/--no-close] [--json]

# Post a manual verdict to the issue.
opentraces capsule verdict <issue-ref> --state fixed|reproduces|inconclusive \
  [--note "..."] [--close]

# Poll an issue for resolution (unblock cue).
opentraces capsule watch <issue-ref> [--timeout 300] [--json]
```

`capsule test` exits early with a clear message if the capsule carries no test
command; use `capsule replay` for intent-only sessions. `--matrix name=v1,v2`
sweeps one consumed dependency and reports which version flips the verdict to `fixed`
(`resolved_in`). The repro is captured, untrusted input — `test` asks for
confirmation before executing it (skip with `--yes`) and runs under
`core/isolation.py`'s minimal env allowlist (see
[Bundle Safety and Sandbox](#bundle-safety-and-sandbox)). A FOREIGN capsule's
command is blocked from running on the host by default.

### Replay Honesty: the Four Properties

`capsule replay`'s packet leads with four named properties, each derived from
a lattice-ranked factor (ADR-0008):

| Property | Derived from | `ok` when |
|----------|---------------|-----------|
| `reproducible` | `env_tier` | `L3` or `L4` (hermetic env) |
| `gradable` | `oracle_trust` | `captured_pass`, `captured_error`, or `declared` |
| `scoped` | `diff_trust` | `exact` (the diff bounds exactly the sealed slice) |
| `sandboxed` | `sandbox_tier` | any real isolation tier above `none` |

`verdict_trust` (`floor` / `low` / `medium` / `high`) is the derived weakest-link
summary — the `min()` over all four factors' lattice positions — kept on the
packet for automation thresholds. **On today's corpus every honest capsule
reports `verdict_trust: floor`** and refuses to claim `reproducible`: no
dependency-pin resolver ships yet, so `env_tier` never rises off its `L0`
floor. That is the honesty contract working as intended, not a bug — trust
rises only when a factor's real underlying state rises (a future resolver for
`env_tier`, a declared oracle or `captured_pass` for `oracle_trust`, a
slice-scoped diff for `diff_trust`, real OS containment for `sandbox_tier`),
never by relabelling a claim.

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
- **Home-path scrub.** The registry `path_anonymizer` tool (issue #143) rewrites
  home paths tail-consuming: `/Users/<name>/secret/.env` becomes
  `/Users/[ot-user-<8hex>]`, the whole tail gone, not just the username segment.
  The operator's own identity tokens (login name, home-dir name) are hashed the
  same way wherever they appear as bare tokens. The `[ot-user-<8hex>]` marker is
  structurally inert (a leading `[` can never start a detected username), so the
  scrub is idempotent by construction.
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

## Bundle Safety and Sandbox

A `--bundle` embeds a hermetic `git archive` of the pinned commit, so
`capsule test --from-bundle` runs even after the commit becomes unreachable.
Before that bundle ships anywhere (`share --publish`, `issue --publish` with
`--bundle`), the exact shipped bundle bytes are scanned for secrets (native
TruffleHog over the extracted archive) as a publish GATE, never a trust
factor: a finding blocks the publish with zero bytes out
(`BundleSecretFindingError`) unless `--i-accept-bundle-findings` is passed to
acknowledge and ship anyway. `bundle.secret_scan` is always stamped on the
envelope — clean or blocked, never the secret byte itself. Well-known
secret-bearing paths (`.env`, keys, certs, `.aws/`, `.netrc`) are excluded
from the archive members before gzip, byte-identical when nothing matches.

`capsule test` runs the repro under `core/isolation.py`'s isolated-subprocess
primitive: an allowlist-only child environment (secrets never inherited), a
redirected `$HOME`, and (where the OS supports it) a probed network-deny
mechanism. The primitive stamps `sandbox_tier` honestly using the ADR-0008
lattice vocabulary (`none`/S0, `jail`/S1, `container`/S2, `microvm`/S3);
today it always reports `none` (S0) — a same-UID `$HOME` redirect is not real
filesystem containment, so the tier never over-claims. Sandbox v1 adds a
block-foreign-by-default GATE on top of that honest label: a FOREIGN
capsule's captured command refuses to run on the host unless you pass
`--unsafe-run-on-host` (no real containment) or `--i-own-isolation` (you
assert you are already inside your own container/VM) — either way the
stamped `sandbox_tier` stays `none` until real OS-level containment lands.

## URL Design

The shareable URL points at one self-contained file, pinned to the immutable publish
commit sha:

```
https://huggingface.co/datasets/<owner>/<repo>/resolve/<commit-sha>/capsules/v1/<id>/capsule.json
```

`publish_capsule` uploads only `capsule.json` + `capsule.md` (not the whole private
bucket). Pinning to the commit sha means the URL is immutable: the file it resolves
to can never silently change, the defining property of the capsule seal
(ADR-0008: an immutable, URL-addressed seal, as opposed to the dataset's growing,
reviewed seal).

### Rendered View (capsule-worker)

A published capsule also renders at a human-friendly URL served by a stateless
Cloudflare Worker (`web/capsule-worker/`), a pure projection over the same frozen
`opentraces.capsule.v1` object, with no re-derivation, no re-redaction, and one
outbound fetch to the HF `capsule.json`:

- `Accept: text/html` → a human page rendering the four signals honestly (untrusted
  content escaped, redaction markers kept verbatim, excluded fields shown as
  "excluded by author" not "broken").
- Progressive JSON endpoints for no-CLI agents: `/summary` → `/index` → `/slice`
  `/context` `/trail` `/repo` `/environment` → `/full`, plus `/skill`. `/full`
  returns the upstream bytes verbatim, byte-identical to `capsule get --json`.
- The worker never serves the heavy "environment" face (bundle tar, runtime pins,
  lockfiles), only the name-only `environment` projection.
- If the fetch/parse fails, it degrades to a pointer at the raw HF `/resolve/` URL
  plus the CLI one-liner, rather than failing outright.

Production domain wiring (`capsules.opentraces.ai`) is a deploy-time operation,
independent of the capsule format itself.

## Self-Sufficiency

Sealing (`create` or `export`) sources the context resume packet from the live
Context Tree projection when present, and falls back to the trace's own bucket
companion (`bucket/contexts/v1/<slug>/<trace>/nodes.jsonl` + layer blobs). A
capsule resolves with zero access to the originating machine. Degraded
captures produce a valid `closure_intent_only` capsule (recorded in
`limitations`), never a crash.

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

# 3. Seal a local capsule. `export` (hidden, still callable) is needed here for
#    --test-command/--setup-command/--consume; the v7-address `create <ref>`
#    covers the plain "just seal this scope" case with a narrower flag set.
opentraces capsule export <trace-id> \
  --product humanduration \
  --test-command "pytest tests/test_parse.py -k parse_iso" \
  --setup-command "pip install -e ." \
  --consume "package:humanduration=git+https://github.com/owner/humanduration@main"

# 4. Share it (publishes to HF, shows consent gate; blocked on a bundle
#    secret-scan finding unless --i-accept-bundle-findings).
opentraces capsule share <trace-id> --publish

# 5. File a GitHub issue embedding the URL (consent gate runs again, once total).
opentraces capsule issue <trace-id> --publish --yes

# 6. A maintainer agent resolves it (read-only).
opentraces capsule get https://huggingface.co/.../capsule.json --json

# 7. The maintainer re-poses the intent and records what happened.
opentraces capsule replay <trace-id> --against HEAD --json
opentraces capsule verdict <issue-ref> --state fixed --close
```

## Accepted Command Options Reference

All options below apply to `export`, `share`, `issue`, and `preview` unless
noted; `create` uses the narrower v7-address flag set documented under
[`capsule create`](#capsule-create--seal-a-local-capsule) instead.

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
| `--product-full-span` | off | `preview` only: opt out of the `--product` radius cap and restore the unbounded min..max episode span (may be slow on large sessions) |
| `--yes` | off | Skip the consent gate (share/issue only, for scripts/agents) |
| `--json` | off | Emit JSON to stdout |
