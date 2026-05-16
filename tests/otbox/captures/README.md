# Captured agent-session artifacts (plan 071/072)

This directory holds snapshot artifacts produced by
`./otbox capture-refresh --scenario <name>`. Each subdirectory contains:
- `snapshot.tar.gz` — the tar archive of the captured box state
- `metadata.json` — capture provenance (timestamp, agent version, schema version, scenario digest)

Plan 072's checkpoints restore from these artifacts. With no artifacts
present (default-CI state for new clones), checkpoints fall back to the
synthetic harness chain (plan 064/068 substrate).

Refresh via `make capture-refresh SCENARIO=<name>` on a substrate
where the named binary is logged in. Plan 073 stands up the Mac Mini
self-hosted runner that automates this.
