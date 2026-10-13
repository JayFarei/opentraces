# opentraces metrics worker

Cloudflare Worker API for package and repo distribution stats.

This service stores snapshots in D1 and exposes two different shapes:

- `GET /v1/projects/:id/summary` for latest numbers only
- `GET /v1/projects/:id/timeseries` for graph points

That split is intentional. Summary cards and charts have different payloads, cache
profiles, and source limitations.

## Why a normal Worker, not Dynamic Workers

For `opentraces`, a standard scheduled Worker is the right fit. The collector logic
is fixed, lightweight, and only needs HTTP fetches plus D1 writes. Cloudflare
Dynamic Workers would make sense later if you want user-defined collectors or
per-project executable code loaded on demand.

## Sources

- PyPI via `pypistats.org`
- Homebrew via `formulae.brew.sh`
- GitHub via the repository REST API

## Important limitation

PyPI can provide real daily download points.

Homebrew's public analytics API only exposes rolling aggregates such as `30d`,
`90d`, and `365d`. That means the Worker can graph Homebrew only as a snapshot
series over time, not as true historical daily installs.

GitHub stars work the same way here: we snapshot `stargazers_count` on a schedule.

## Setup

```bash
cd web/metrics-worker
npm install
npx wrangler login
npx wrangler d1 create opentraces-metrics
```

For this open-source repo, the tracked [`wrangler.jsonc`](./wrangler.jsonc) keeps
`database_id` as `replace-me`. For local development, copy
[`wrangler.local.jsonc.example`](./wrangler.local.jsonc.example) to
`wrangler.local.jsonc`, add your real D1 `database_id`, and the npm scripts will
use that local override automatically.

Apply the versioned D1 migrations before running or deploying the Worker:

```bash
node scripts/run-wrangler.mjs d1 migrations apply opentraces-metrics --local
# When preparing a production deployment:
node scripts/run-wrangler.mjs d1 migrations apply opentraces-metrics --remote
```

The migrations also work on existing databases initialized with `schema.sql`:
the baseline preserves their history, and the next migration creates and
backfills `metrics_latest`. Add `"migrations_dir": "migrations"` to the D1 binding
in any existing local config. `schema.sql` describes the final schema; use
migrations for upgrades so existing history is backfilled before the new Worker
starts reading `metrics_latest`.

Optional secrets:

```bash
npx wrangler secret put ADMIN_TOKEN
npx wrangler secret put GITHUB_TOKEN
```

`GITHUB_TOKEN` is optional but recommended to avoid low unauthenticated rate limits.

For local development, copy [`.dev.vars.example`](./.dev.vars.example) to `.dev.vars` and fill in any secrets you want available to `wrangler dev`.

## Deploy

```bash
npm run deploy
```

After deploy, note the `workers.dev` URL and set it in the site as `STATS_API_BASE_URL`.

## Endpoints

`GET /v1/projects`

Returns the configured project catalog.

`GET /v1/projects/opentraces/summary`

Returns the latest known numbers for PyPI, Homebrew, and GitHub.

The configured Homebrew formula for `opentraces` is `JayFarei/opentraces/opentraces`.

`GET /v1/projects/opentraces/timeseries?source=pypi&metric=downloads&window=90d`

Returns graphable points for one or more metrics.

`POST /v1/internal/collect?project=opentraces`

Manually triggers a collection run. Requires `Authorization: Bearer <ADMIN_TOKEN>`.

## Storage and edge caching

Collection writes history and `metrics_latest` together in a D1 transaction.
Summary and health queries read only the small latest table; the seed check stops
at the first project row. The scheduled collector deletes only snapshot rows
with `as_of` older than 90 days, using a retention index. Daily metrics are kept
indefinitely, and latest values remain available even if a source stops reporting.
Timeseries windows longer than 90 days can therefore contain older daily points,
but snapshot points are limited by retention.

Successful public summary and timeseries GET responses use `caches.default` with
the full URL (including query parameters) as the key and a 300-second lifetime.
Hits bypass seeding and D1 queries. CORS headers are applied on both hits and
misses using the current `CORS_ORIGIN`; POST, OPTIONS, and error responses are
not stored in this cache. Collection results become visible after cache expiry.
Cloudflare's [Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)
is local to each data center, so verification must account for where requests land.

## Issue #368 verification after deployment

Local checks cannot establish the production daily read budget. After applying
the migrations and deploying, compare at least three complete UTC days before
and after deployment for the same `opentraces-metrics` D1 database. Exclude the
deployment day: the migration's one-time backfill reads existing history.

Baseline from issue #368 (collected 13 October 2026):

| UTC date | rowsRead | readQueries | rowsWritten |
| --- | ---: | ---: | ---: |
| 10 October 2026 | 2,727,105 | 246 | 4,478 |
| 11 October 2026 | 2,950,881 | 268 | 4,510 |
| 12 October 2026 | 3,942,664 | 358 | 4,542 |

Over 11–13 October, the old seed check read 4,360,728 rows in 471 calls
(about 9,260 per call); the old summary read 6,298,602 rows in 170 calls
(about 37,050 per call).

- Use `d1AnalyticsAdaptiveGroups` to record daily rows read, read queries, and
  rows written, alongside Worker request counts and traffic differences. Each
  of the three complete post-deployment days must read fewer than 500,000 rows
  at comparable traffic. The post-deployment results are **pending**.
- Use `d1QueriesAdaptiveGroups` to compare the old queries with `SELECT 1 ...
  LIMIT 1` and the `metrics_latest` summary query. Confirm their per-call read
  cost stays bounded as snapshot history grows; include health and retention.
- Repeat each public GET URL from the same location within 300 seconds; confirm
  identical payloads (including `generatedAt`), correct CORS headers, and no
  extra seed/summary/timeseries D1 calls in logs or query analytics. Change a
  timeseries query parameter to verify separate keys. Repeat after expiry and
  confirm a fresh `generatedAt` and database read. OPTIONS must retain CORS too.
- Check that a scheduled run removes snapshots older than the 90-day cutoff,
  retains snapshots at/after the cutoff and all daily rows, and leaves summary
  values intact. Compare timeseries with the retained history.

Attach the before/after evidence to the PR. This change preserves UI and API
shapes; screenshots and a recording are needed if subsequent changes affect
visible behavior.

## Integration Contract For Next.js

The site should treat the Worker as an external JSON API with this base URL:

```bash
STATS_API_BASE_URL=https://<your-worker-domain>
```

The current Next.js adapter reads `STATS_API_BASE_URL` first and falls back to
`NEXT_PUBLIC_STATS_API` only if needed. Prefer `STATS_API_BASE_URL` so the fetch
stays server-side.

### Recommended fetch model

- Fetch from Server Components, Route Handlers, or other server-only code.
- Cache at the app layer with `next: { revalidate: 300 }`.
- Treat `summary` as the source for KPI cards and `timeseries` as the source for charts.
- Do not assume every source always returns data. Each source has a `status` of `ok`, `unavailable`, or `error`.

### Stable endpoints

- `GET /v1/projects`
- `GET /v1/projects/:id/summary`
- `GET /v1/projects/:id/timeseries?source=<source>&metric=<metric>&window=<Nd>`

### Supported project id

- `opentraces`

### Summary response shape

`GET /v1/projects/opentraces/summary`

- `project`: project id
- `generatedAt`: ISO timestamp for the response build time
- `sources.pypi`: latest PyPI metrics plus package metadata
- `sources.homebrew`: latest Homebrew snapshot metrics plus formula metadata
- `sources.github`: latest GitHub snapshot metrics plus repo metadata

Current metric keys exposed in `summary.sources.*.metrics`:

- PyPI: `downloads`, `recent_last_day`, `recent_last_week`, `recent_last_month`
- Homebrew: `installs_30d`, `installs_90d`, `installs_365d`
- GitHub: `stars`, `forks`

Each metric object has:

- `value`: numeric value
- `unit`: unit label such as `downloads`, `installs`, or `stars`
- `bucket`: metric bucket key. For daily PyPI points this is `YYYY-MM-DD`. For snapshots this is an ISO timestamp.
- `granularity`: `day` or `snapshot`
- `asOf`: ISO timestamp when that metric snapshot was collected
- `meta`: source-specific metadata or `null`

### Timeseries query contract

`GET /v1/projects/opentraces/timeseries?source=pypi&metric=downloads&window=30d`

Query params:

- `source`: `pypi`, `homebrew`, or `github`
- `metric`: source-specific metric name
- `window`: day window in `<number>d` format, for example `30d` or `90d`

Response shape:

- `project`: project id
- `window`: echoed window value
- `generatedAt`: ISO timestamp for the response build time
- `series`: array of metric series matching the query

Each `series[]` item contains:

- `source`
- `metric`
- `unit`
- `granularity`
- `points`

Each `points[]` item contains:

- `bucket`
- `asOf`
- `value`
- `meta`

### Source semantics

- PyPI `downloads` is real daily history.
- Homebrew metrics are rolling aggregate snapshots, not true daily history.
- GitHub metrics are snapshot series collected on the Worker schedule.

### Frontend guidance

- Use `summary` for top-line counts.
- Use `timeseries?source=pypi&metric=downloads` for the homepage download chart.
- If `status !== "ok"` for a source, degrade gracefully and render a fallback state instead of failing the page.
- Browser-side direct fetches are only appropriate if the request origin is allowed by the Worker's `CORS_ORIGIN`. Server-side fetches avoid that constraint.

## Next.js Example

```ts
const baseUrl = process.env.STATS_API_BASE_URL!;

const summary = await fetch(`${baseUrl}/v1/projects/opentraces/summary`, {
  next: { revalidate: 300 },
}).then((res) => res.json());

const series = await fetch(
  `${baseUrl}/v1/projects/opentraces/timeseries?source=pypi&metric=downloads&window=30d`,
  { next: { revalidate: 300 } },
).then((res) => res.json());
```
