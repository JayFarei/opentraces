# Leased capture provisioning and source inventory

This document records the A3 leased-box spike and the source contract implemented by
`opentraces.capture`. It is implementation evidence, not a second setup path. Both
persistent installations and leased workspaces call `Capture.open(plan)`, inject the
returned bindings into the observed actor, and call `finish(deadline)` once.

## Reproducible spike

The spike ran on 2026-07-13 with Crabbox 0.38.0 using a real local-container lease,
an explicit `ubuntu:24.04` image, and `TMPDIR=$HOME/crabbox-tmp`. The worktree was
synced into the lease, Python 3.12.3 and Git 2.43.0 were confirmed, the package was
installed into an in-box virtual environment, and the public capture contract suite
ran inside the box:

```text
........                                                                 [100%]
8 passed in 4.01s
command complete in 4.398s
```

The suite exercised a real inline OTel receiver process, a deliberately killed
required receiver, persistent and leased JSONL ingestion, watcher drain, Git
maturation, canonical event writes, bucket projection, and raw-material placement
parity. The lease was then explicitly deleted. No host daemon or service manager was
used by the leased path.

## Finalizable source families

| Capture source | Box-side requirement | Leased verdict | Finalization owned by `finish()` | Known limitation |
|---|---|---|---|---|
| `session_jsonl` | An explicit actor, session id, and readable session JSONL path | Capturable for Claude, Codex, or Pi when the runtime supplies its native transcript path | Existing `ingest_one_session`; this preserves the parser, security, Trace assembly, index, and bucket write chain | Capture does not discover an arbitrary actor's transcript inside an isolated box; the actor adapter must supply the path |
| `telemetry` | Actor receives the returned OTLP endpoint and raw-body directory bindings | Capturable; the leased adapter starts a real inline receiver and waits for its session snapshot | Existing `flush_session_to_project`, then the canonical Context event writer | Only actors that emit compatible OTel/raw-body evidence contribute this view; a stopped or silent receiver is honestly `unavailable`/`missing` |
| `watcher` | Workspace is local to the lease and readable by the observer | Capturable | One watcher poll plus `reconcile_watcher_observations` | Observation begins when Capture opens; mutations outside that lifecycle are not inferred |
| `git` | Workspace is a Git repository with the OpenTraces event ref available or bootstrap-able | Capturable | Existing `mature_trails` under the caller's absolute deadline | Empty repositories produce a valid empty maturation; unresolved anchors remain reported by the existing summary |
| `bucket` | A preceding source has materialized a Trace through the sanctioned ingest path | Capturable | Verifies the canonical trace envelope and manifest produced by existing bucket custody | It cannot manufacture a Trace when no source produced one; that is `unavailable` rather than a thinner success |

Every source finalizer runs in an isolated child process. The parent spends only the
remaining wall-clock budget, kills an overrun finalizer, persists its source status,
and continues assembling the result. Requested does not mean captured: any requested
missing/partial source makes the overall result partial, even when it was not marked
required by the caller.

## Capability inventory

The rows below distinguish independently finalizable source families from evidence
that is produced inside one of those families. This prevents side effects from being
advertised as extra capture sources.

| Capability | How it reaches the one Trace | Leased verdict and requirement |
|---|---|---|
| Claude Code | Native JSONL through `session_jsonl`; model-boundary evidence through `telemetry`; installed tool hooks may add Trail evidence | Capturable when the runner supplies the transcript and injects Capture bindings. Capture does not mutate global Claude settings in a lease |
| Codex CLI | Native rollout JSONL through `session_jsonl`; hook sidecars can add lifecycle evidence | Capturable when the runner supplies the rollout path. Global Codex hook installation is outside the lease lifecycle |
| Pi | Native session JSONL through `session_jsonl`; the Pi bridge can add provider and Trail evidence | Capturable when the runner supplies the session path and provisions the existing Pi bridge; bridge installation is not implied by `Capture.open` |
| Tool-boundary observations | Existing agent hooks append observations to the canonical Trail event log; watcher/Git finalizers drain and mature them | Conditionally capturable. The observed runtime must already support and receive its hook command. `CaptureBindings.hook_commands` is empty until a runtime-specific adapter supplies one |
| Raw provider bodies | The returned private raw-body directory is watched by the inline receiver and flushed with telemetry | Capturable for compatible emitters. Retention remains explicit; raw bodies never become a public artifact by this lifecycle |
| Trail and Context companions | Existing append-event and bucket projection paths create companions from JSONL, telemetry, tool, watcher, and Git evidence | Capturable. No leased-only event schema or writer exists |
| Security | JSONL ingest uses the shared sanitizer; telemetry/raw-body handling uses the existing private retention path; bucket result records the actual Trace security state | Capturable through the producing source, not an independent source. Artifact-aware external clearance remains a later custody operation |
| Actor metadata | Preserved by the selected native parser and separately pinned observer/product versions in `CaptureResult` | Capturable to the fidelity of the supplied adapter evidence; Capture does not invent missing runtime identity |
| Search index | Existing ingest/index side effects run when `session_jsonl` produces a Trace | Capturable as an ingest side effect. It is not independently claimed complete by the source manifest |
| Proxy/interception | No production adapter exists; the schema vocabulary remains reserved | Not capturable in A3 and not a prerequisite for Capture, browser drive, or agent drive |
| Token-level RL telemetry | Requires training-client integration rather than post-hoc observation | Not capturable; step spans are not represented as equivalent token/logprob evidence |

## Persistent and leased placement

Persistent placement keeps the current OpenTraces root and external service endpoint;
it does not reinstall hooks, rewrite settings, or replace the existing daemon. Leased
placement redirects only `OT_OPENTRACES_DIR`, starts its receiver inline, and records
all runtime/log/result files under the requested result directory. Both paths use the
same source finalizers and canonical writers. Telemetry accepts the same fresh,
internally consistent snapshot generation in both placements: the leased receiver is
owned and must additionally stamp that generation quiescent after ingress stops,
while the persistent daemon stays live. Until that daemon exposes a finish barrier
acknowledgment, a staged persistent generation remains honestly partial because
finish-tail coverage cannot be proven.

Capture security has no top-level privacy tier. `CapturePlan.security_tools=None`
uses the project's configured `security.<tool>.enabled` flags; an explicit tuple sets
the exact tool flags for the isolated finalizer. The result records those configured
tools separately from the `tools_applied` manifest observed on a finalized Trace.

Placement parity is evaluated over stored Trace, Context companion, Trail companion,
security result, and three-view completeness. The verifier normalizes only declared
trace ids and workspace/bucket roots after capture. Optional slicer spans compare
their deterministic coordinates; display labels compare only when labeler provenance
matches. Placement, timing, pins, available source capabilities, and explicit
limitations are allowed differences.

When a caller requires observer/product separation, it supplies the live product PID
and a bounded `product_under_test_version_probe` argv. `finish()` observes the live
process command and executable identity, digests the exact probe launcher, executes
interpreted-script probes through the observed runtime (preserving macOS virtualenv
launcher context), and binds that observed version to the caller's separate claim.
Missing, dead, self, unrelated, unprobeable, or version-mismatched identities remain
partial with a named limitation; a live PID alone is never separation proof.

## Follow-on integration contract

The placement owner must materialize the workspace, open Capture, inject `bindings.env`
and the endpoint/path bindings into the real actor, then call `finish()` before it
collects artifacts or releases the box. It must not call JSONL ingest, OTel flush,
watcher reconciliation, Git maturation, or bucket projection itself. Runtime-specific
hook installation remains an adapter concern and must be declared unavailable when it
cannot be performed inside the lease.
