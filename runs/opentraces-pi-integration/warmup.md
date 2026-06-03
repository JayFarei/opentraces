# OpenTraces Pi Integration — Goal Session Warm-up

Date: 2026-06-02
Plan: `kb/plans/091-opentraces-pi-extension-and-bucket-search.md`
Run log: `runs/opentraces-pi-integration/log.md`

## Outcome to keep in mind

This is a dual deliverable:

1. **New OpenTraces harness support** for Pi: parser/bridge/installer/resumer under `src/opentraces/capture/pi/`, registry key `pi`, `opentraces init --agent pi`, and standard TraceRecord + Trace Trails + Context Tree + bucket v2 output.
2. **New Pi-native OpenTraces package/extension**: `packages/opentraces-pi/`, installable via `pi install npm:opentraces-pi`, with Pi extension, skill, prompts, `/ot-*` commands, `ot_*` tools, setup/status UX, and package-gallery metadata.

The invariant is convergence: Pi traces must use the same OpenTraces substrates as Claude/Codex, not a Pi-only trace format, bucket layout, search index, or capture mode.

## Locked decisions from planning

- Package name: `opentraces-pi`; canonical home: `packages/opentraces-pi/`.
- Harness/agent key: `pi`; TraceRecord agent name should be `pi`.
- Python bridge owns v1 persistence/validation: sidecar schema validation, content-addressed blobs, raw-body defaults, Trail state, ingest spawning, dedupe/finalization cursors.
- TypeScript extension stays thin: Pi event normalization, fail-open bridge calls, UX/tools/commands/renderers, and argv-safe `pi.exec` wrappers around stable OpenTraces `--json` surfaces.
- No Pi-only capture modes. Use existing concepts: capture enrollment/consent + raw-body retention/opt-in. Raw provider bodies default off/local/security-gated.
- Package install must be quick/reversible. No npm postinstall that silently installs Python, services, auth, bucket remotes, or security tools.
- `/ot-setup` defaults to minimal local capture readiness, asks once before enabling capture (default yes), and marks terminal/auth-heavy steps as `needs_terminal`.
- OpenClaw is out of scope.
- Pi package registry: publish as npm sub-package with `keywords: ["pi-package", ...]`, `repository.directory: "packages/opentraces-pi"`, intended `files`, peer deps for Pi API, and gallery image/video when available. Separate Git submodule/repo only if Pi gallery indexing proves monorepo sub-package publication cannot work.

## Relevant research already done

Pi docs/examples read:

- `/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/extensions.md`
- `docs/packages.md`, `docs/session-format.md`, `docs/sessions.md`, `docs/sdk.md`, `docs/tui.md`, `docs/rpc.md`, `docs/json.md`, `docs/settings.md`, `docs/compaction.md`
- `examples/extensions/provider-payload.ts`, `dynamic-tools.ts`, `custom-compaction.ts`, `message-renderer.ts`, `structured-output.ts`, `git-checkpoint.ts`, `github-issue-autocomplete.ts`, `system-prompt-header.ts`, `todo.ts`

Pi package precedents inspected:

- `mksglu/context-mode`
- `nicobailon/pi-subagents`
- `nicobailon/pi-web-access`
- `MattDevy/pi-extensions/packages/pi-simplify`
- `BlazeUp-AI/Observal/packages/pi-extension` (`observal-pi`)
- `mprokopov/pi-otel-telemetry`
- `saravananravi08/pi-langfuse-extension` (`@ravan08/pi-langfuse`)

Important lessons:

- Observal is the closest reliability/distribution precedent: monorepo sub-package, `pi-package` keyword, `repository.directory`, incremental `agent_end` push, final `session_shutdown` flush, stale-session recovery, cursor/dedupe state, footer status, `/obs-sync`, fail-open.
- `pi-otel-telemetry` validates Pi lifecycle event vocabulary for sessions/prompts/turns/tools/model/compaction/provider request, but OpenTraces should not route canonical evidence through generic OTEL.
- `@ravan08/pi-langfuse` validates prompt/turn/tool/usage/cost/error mapping, but its remote observability defaults differ from OpenTraces local-first privacy posture.

## Otbox understanding / coverage design

Otbox coverage is not optional. For harness support, unit tests are insufficient; v1 requires full-world otbox evidence like Claude/Codex.

Otbox mechanics to preserve:

- Journeys live at `tests/otbox/catalogue/journeys/*.toml`; add coverage by adding TOML, not runner special cases.
- Checkpoints live under `tests/otbox/checkpoints/` and register through `tests/otbox/checkpoints/__init__.py`.
- Captured-session checkpoints are artifact-preferred with synthetic/fixture fallback or missing-artifact SKIP behavior.
- Simulated-user scenarios live at `tests/otbox/simulated_users/scenarios/*.toml`; `make capture-refresh SCENARIO=<name>` drives real binaries when present and SKIPs cleanly when missing.
- `tests/otbox/journey.py::_captured_session()` exposes checkpoint audit notes as template vars. Pi needs `c_captured_pi_session_audit` support analogous to Codex.
- `tests/otbox/simulated_users/scenario.py::SUPPORTED_AGENTS` currently omits `pi`; implementation must add it.
- `make otbox-inventory` is the strict Click × journey coverage gate. If Pi adds new CLI trajectories in plan 063, gold journeys must cover any agent-facing requirements.

Plan 091 now expects these Pi otbox additions:

- Checkpoints: `c-captured-pi-real-session`, `c-captured-pi-provider-context`, `c-captured-pi-compacted-session`, `c-captured-pi-branch-session`, `c-captured-pi-readonly-session`, `c-captured-pi-security-session`, `c-mixed-agent-pi-bucket`, `c-pi-full-parity-latest`.
- Scenario TOMLs: `pi-linear-edit`, `pi-provider-context`, `pi-compaction`, `pi-branch-rewind`, `pi-readonly-search`, `pi-security-redaction`, `pi-setup-status`.
- Journeys: `pi-package-gallery-manifest`, `pi-setup-dry-run`, `pi-extension-capture-linear`, `pi-extension-trail-anchor`, `pi-context-tree-provider-fidelity`, `pi-compaction-branch-fidelity`, `pi-readonly-recursion-guard`, `pi-security-sanitize-captured-content`, `pi-incremental-recovery`, `mixed-agent-bucket-parity-pi`, `pi-full-parity-latest`.

Default CI contract: Tier 0 must be offline/deterministic. Live Pi refresh is opt-in and should SKIP without a logged-in `pi` binary.

## First implementation sequence recommendation

1. Contract/fixtures first: freeze sidecar schema and create minimal native Pi JSONL + sidecar fixtures for linear edit, tool result, provider context, compaction, and readonly search.
2. Python bridge/parser: implement enough to turn the linear fixture into a valid TraceRecord and bucket v2 export.
3. Register `pi` in capture registry and `opentraces init --agent pi` paths.
4. Add package skeleton `packages/opentraces-pi/` with manifest/build/typecheck and stub tools/commands.
5. Add installer/status (`opentraces setup pi --dry-run --json`) and Pi package metadata checks.
6. Add Trail/Context Tree parity, then bucket/search warm projection.
7. Add otbox checkpoint family and journeys early enough that they guide the final integration, not after the fact.
8. Run docs-update after code surfaces stabilize.

## Verification commands to surface before completion

Focused/local:

```bash
npm --prefix packages/opentraces-pi run build
npm --prefix packages/opentraces-pi pack --dry-run
.venv/bin/python -m pytest tests/capture/test_parser_pi.py -q
.venv/bin/python -m pytest tests/capture/test_pi_bridge.py -q
.venv/bin/python -m pytest tests/capture/test_pi_trail_capture.py -q
.venv/bin/python -m pytest tests/capture/test_pi_context_tree_capture.py -q
.venv/bin/python -m pytest tests/cli/test_pi_installer.py tests/cli/test_pi_extension_tools.py -q
.venv/bin/python -m pytest tests/core/test_bucket_mixed_agent_manifest.py tests/integration/test_trace_record_stability.py -q
opentraces setup pi --dry-run --json
```

Otbox:

```bash
./otbox matrix --journey 'pi-*'
./otbox matrix --checkpoint 'c-captured-pi-*'
./otbox matrix --journey mixed-agent-bucket-parity-pi
make otbox-inventory
make otbox-journeys
make otbox-agent-session
make capture-refresh SCENARIO=pi-linear-edit  # SKIP cleanly without real pi; refresh artifact when available
```

Broad:

```bash
.venv/bin/python -m pytest tests/ -v
```

## Boundaries / do not touch

- Do not touch unrelated `tests/search_eval/SEARCH-EVAL.md` or `kb/blog/` unless the user explicitly asks.
- Do not weaken synthetic fallback/default-CI SKIP behavior in otbox.
- Do not add a Pi-only bucket layout, index, or storage format.
- Do not route canonical OpenTraces evidence through OTEL/Langfuse/Observal APIs; those are precedents only.
- Do not require real Pi auth/binary for default CI.
