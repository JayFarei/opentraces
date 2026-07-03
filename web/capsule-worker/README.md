# opentraces capsule render worker

A stateless Cloudflare Worker that renders a published capsule from its URL. Given `capsules.opentraces.ai/<owner>/<repo>/<id>@<sha>` it fetches the **one** immutable, already-redacted `capsule.json` from HuggingFace and serves it two ways:

- a **human HTML page** (`Accept: text/html`) — the four signals rendered honestly;
- **progressive JSON endpoints** for no-CLI agents: `/summary` -> `/index` -> `/slice` `/context` `/trail` `/repo` `/environment` -> `/full`, plus `/skill`.

It is a pure projection over the frozen `opentraces.capsule.v1` envelope (the frozen data contract: `kb/projects/opentraces/capsule-microsite-handoff/HANDOFF-capsule-microsite-data-contract.md`). No re-derivation, no re-redaction, one outbound fetch, and it never serves the heavy "environment face".

## The six honesty rules (all covered by tests)

1. **Untrusted content** — every captured string is escaped; the untrusted banner is shown.
2. **Never un-redact** — the redaction manifest is counts-only; `[REDACTED]`/`[EXCLUDED:…]` markers stay verbatim.
3. **Privacy is author intent** — excluded fields render "excluded by author", never "broken".
4. **Limitations are the contract** — `limitations[]` is rendered verbatim.
5. **Replay is a claim** — `render_state.replay` (always `replay_unverified` in v1) renders as a claim, not proof.
6. **Version-gate** — a newer `opentraces.capsule.<x>` degrades to "needs a newer viewer".

Plus: **never serve the environment face** — the worker fetches only `capsule.json` and renders only the name-only `environment` projection; it never touches `capsule.bundle.tar.gz` / `runtime.json` / `lock.txt` / `pin.json`.

## Byte-identity to the CLI

`/full` returns the upstream bytes verbatim, so it is byte-identical to `opentraces capsule open --json`. Section endpoints serialize their carried sub-object with `canonicalStringify` (proven byte-identical to Python `json.dumps(sort_keys=True, indent=2)` against the frozen sample). The full CLI-parity fixture is wired by the integrator once the capsule envelope is frozen by the foundation slice.

## Graceful degrade

If the worker cannot fetch/parse the blob it returns a pointer to the raw HF `/resolve/` URL (and the markdown fallback + CLI one-liner) instead of failing — the immutable blob serves agents even when the viewer is down.

## Develop / test

```sh
npm install
npm test          # vitest under the Workers runtime (miniflare); no deployed infra
npm run typecheck # tsc on src
npm run dev       # wrangler dev (local)
```

Tests inject a fixture `fetcher` (the committed frozen sample), so the suite is green in CI with no network and no deployed infrastructure.

## Deploy (post-merge, excluded from this change)

`npm run deploy` (wrangler). Production domain wiring (`capsules.opentraces.ai`) and any route/DNS setup are post-merge operations and intentionally out of scope here.
