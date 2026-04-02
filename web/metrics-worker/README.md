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

Put the resulting `database_id` into [`wrangler.jsonc`](./wrangler.jsonc), then apply the schema:

```bash
npx wrangler d1 execute opentraces-metrics --local --file=schema.sql
npx wrangler d1 execute opentraces-metrics --remote --file=schema.sql
```

For this open-source repo, the tracked [`wrangler.jsonc`](./wrangler.jsonc) keeps
`database_id` as `replace-me`. For local development, copy
[`wrangler.local.jsonc.example`](./wrangler.local.jsonc.example) to
`wrangler.local.jsonc`, add your real D1 `database_id`, and the npm scripts will
use that local override automatically.

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

The configured Homebrew formula for `opentraces` is `jayfarei/opentraces/opentraces`.

`GET /v1/projects/opentraces/timeseries?source=pypi&metric=downloads&window=90d`

Returns graphable points for one or more metrics.

`POST /v1/internal/collect?project=opentraces`

Manually triggers a collection run. Requires `Authorization: Bearer <ADMIN_TOKEN>`.

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
