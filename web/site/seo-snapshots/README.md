# seo-snapshots — the standings store

Append-only JSONL ledgers for the SEO/AEO monitoring loop (`../SEO-AEO-LOOP.md`). This mirrors opentraces' own append-only-event-log mental model: every monitor run and every experiment is a record, never an in-place edit, so history, diffs, and PR review come for free. This is the Phase 0 substrate; the credentialed signals (GSC, BigQuery, the GEO panel) write into the same files in later phases.

## Files

| File | What it holds | Written by |
|---|---|---|
| `standings.jsonl` | One record per `(run, surface)` — the monitor's observations (crawler_hits today; seo_check via `seo-check --write`; indexnow on submit; later GSC/GEO/CrUX). Keyed by `(run_date_bucket, deploy_sha, surface)`. | `scripts/seo/*.mjs` |
| `change-ledger.jsonl` | One record per experiment-loop change. The data model that the change-budget lock (never >1 `in_flight` per page) reads; enforcement is done by the experiment loop, Phase 1+ (no Phase 0 writer). SSoT for keep/revert verdicts. | experiment loop (human-gated) |
| `community-actions.jsonl` | Off-site / community actions (the dominant lever, §4a). Agent detects + drafts; a human posts. | human + agent drafts |

Schemas for each record type live in `schemas/`.

## Conventions

- **Append-only.** Never rewrite a line; correct by appending a newer record. The latest record for a key wins.
- **Idempotency key** for `standings.jsonl` is `(run_date_bucket, deploy_sha, surface)`. NOTE: Phase 0 writers append **unconditionally** — the §3 "duplicate fire is a no-op" reconciliation (read-store-then-skip) is a Phase 1 scheduler step, not yet wired. "Latest per key wins" holds; the no-op safety does not yet.
- **Diff against a trailing baseline** (median of the last 3–5 runs), never the single prior run — this absorbs noise. AEO/GEO signals are *probabilistic*: never act on a single sample.
- **Timestamps** are ISO 8601 UTC.

## Honest scope

Phase 0 (this) is deterministic and free. The writers are `crawler-report` (`crawler_hits`), `seo-check --write` (`seo_check`), and `indexnow-submit --submit` (`indexnow`). It does **not** measure rankings or AI citations yet — that needs the Phase 1/2 credentials (GSC + the GEO panel) — and several Phase 0 pieces are parser/lib-ready but not yet wired to a live source (crawler log drain, the AI-referral channel, IndexNow auto-fire, the scheduler). See `../SEO-AEO-LOOP.md` → "Phase 0 — implementation status" for the honest wired-vs-deferred breakdown, and §7 for the phased rollout + cadence verdict (monitor every 2–3 days; change at most once per page per 2–4 week window).
