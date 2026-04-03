type SourceName = "pypi" | "homebrew" | "github";
type Granularity = "day" | "snapshot";

interface Env {
  ADMIN_TOKEN?: string;
  CORS_ORIGIN?: string;
  GITHUB_TOKEN?: string;
  METRICS_DB: D1Database;
}

interface ProjectConfig {
  id: string;
  pypiPackage: string;
  homebrewFormula?: string;
  githubOwner: string;
  githubRepo: string;
}

interface MetricRow {
  projectId: string;
  source: SourceName;
  metric: string;
  bucket: string;
  granularity: Granularity;
  unit: string;
  value: number;
  asOf: string;
  meta?: Record<string, unknown>;
}

interface PypiRecentResponse {
  data: {
    last_day: number;
    last_week: number;
    last_month: number;
  };
  package: string;
  type: string;
}

interface PypiOverallPoint {
  category: string;
  date: string;
  downloads: number;
}

interface PypiOverallResponse {
  data: PypiOverallPoint[];
  package: string;
  type: string;
}

interface HomebrewAnalyticsItem {
  formula: string;
  count: string;
  percent: string;
}

interface HomebrewAnalyticsResponse {
  category: string;
  start_date: string;
  end_date: string;
  items: HomebrewAnalyticsItem[];
}

interface GitHubRepoResponse {
  full_name: string;
  html_url: string;
  stargazers_count: number;
  forks_count: number;
  open_issues_count: number;
}

interface CollectSourceResult {
  status: "ok" | "unavailable" | "error";
  detail?: string;
  inserted?: number;
}

const PROJECTS: Record<string, ProjectConfig> = {
  opentraces: {
    id: "opentraces",
    pypiPackage: "opentraces",
    homebrewFormula: "jayfarei/opentraces/opentraces",
    githubOwner: "JayFarei",
    githubRepo: "opentraces",
  },
};

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "public, max-age=300",
};

export default {
  async fetch(request, env, ctx): Promise<Response> {
    const url = new URL(request.url);
    const method = request.method.toUpperCase();

    if (method === "OPTIONS") {
      return withCors(new Response(null, { status: 204 }), env);
    }

    if (method === "GET" && url.pathname === "/health") {
      const staleness = await env.METRICS_DB.prepare(
        "SELECT source, MAX(as_of) AS last_collected FROM metrics_snapshots GROUP BY source",
      ).all<{ source: string; last_collected: string }>();

      const sources: Record<string, { lastCollected: string; staleMinutes: number }> = {};
      const now = Date.now();
      for (const row of staleness.results ?? []) {
        const ageMs = now - new Date(row.last_collected).getTime();
        sources[row.source] = {
          lastCollected: row.last_collected,
          staleMinutes: Math.round(ageMs / 60000),
        };
      }

      const staleThresholdMinutes = 240; // 4 hours (cron is every 3)
      const allHealthy = Object.values(sources).every((s) => s.staleMinutes < staleThresholdMinutes);

      return withCors(json({ ok: allHealthy, now: new Date().toISOString(), sources }), env);
    }

    if (method === "GET" && url.pathname === "/v1/projects") {
      return withCors(json({
        projects: Object.values(PROJECTS).map((project) => ({
          id: project.id,
          pypiPackage: project.pypiPackage,
          homebrewFormula: project.homebrewFormula ?? null,
          githubRepo: `${project.githubOwner}/${project.githubRepo}`,
        })),
      }), env);
    }

    if (method === "POST" && url.pathname === "/v1/internal/collect") {
      if (!isAuthorized(request, env)) {
        return withCors(json({ error: "Unauthorized" }, { status: 401 }), env);
      }

      const projectId = url.searchParams.get("project");
      const project = getProject(projectId);
      const result = project
        ? await collectProject(env, project)
        : await collectAllProjects(env, ctx);

      return withCors(json(result), env);
    }

    const match = url.pathname.match(/^\/v1\/projects\/([^/]+)\/(summary|timeseries)$/);
    if (!match) {
      return withCors(json({ error: "Not found" }, { status: 404 }), env);
    }

    const [, projectId, view] = match;
    const project = getProject(projectId);
    if (!project) {
      return withCors(json({ error: `Unknown project '${projectId}'` }, { status: 404 }), env);
    }

    await ensureSeedData(env, project);

    if (view === "summary") {
      return withCors(json(await buildSummary(env, project)), env);
    }

    return withCors(json(await buildTimeseries(env, project, url.searchParams)), env);
  },

  async scheduled(_controller, env, ctx): Promise<void> {
    ctx.waitUntil(collectAllProjects(env, ctx));
  },
} satisfies ExportedHandler<Env>;

function getProject(projectId: string | null): ProjectConfig | null {
  if (!projectId) return null;
  return PROJECTS[projectId] ?? null;
}

function isAuthorized(request: Request, env: Env): boolean {
  if (!env.ADMIN_TOKEN) {
    return false;
  }

  const header = request.headers.get("authorization");
  return header === `Bearer ${env.ADMIN_TOKEN}`;
}

async function ensureSeedData(env: Env, project: ProjectConfig): Promise<void> {
  const row = await env.METRICS_DB.prepare(
    "SELECT COUNT(*) AS count FROM metrics_snapshots WHERE project_id = ?1",
  )
    .bind(project.id)
    .first<{ count: number }>();

  if (!row || row.count === 0) {
    await collectProject(env, project);
  }
}

async function collectAllProjects(env: Env, _ctx: ExecutionContext) {
  const entries = await Promise.all(
    Object.values(PROJECTS).map(async (project) => [project.id, await collectProject(env, project)] as const),
  );

  for (const [projectId, projectResult] of entries) {
    for (const [source, outcome] of Object.entries(projectResult.sources)) {
      if (outcome.status === "ok") {
        console.log(`[cron] ${projectId}/${source}: ok (${outcome.inserted} rows)`);
      } else {
        console.error(`[cron] ${projectId}/${source}: ${outcome.status} - ${outcome.detail}`);
      }
    }
  }

  return {
    collectedAt: new Date().toISOString(),
    projects: Object.fromEntries(entries),
  };
}

async function collectProject(env: Env, project: ProjectConfig) {
  const collectedAt = new Date().toISOString();
  const [pypi, homebrew, github] = await Promise.all([
    collectPyPI(env, project, collectedAt),
    collectHomebrew(env, project, collectedAt),
    collectGitHub(env, project, collectedAt),
  ]);

  return {
    project: project.id,
    collectedAt,
    sources: {
      pypi,
      homebrew,
      github,
    },
  };
}

async function collectPyPI(env: Env, project: ProjectConfig, collectedAt: string): Promise<CollectSourceResult> {
  try {
    const base = `https://pypistats.org/api/packages/${encodeURIComponent(project.pypiPackage)}`;
    const [recent, overall] = await Promise.all([
      withRetry(() => fetchJson<PypiRecentResponse>(`${base}/recent`)),
      withRetry(() => fetchJson<PypiOverallResponse>(`${base}/overall?mirrors=true`)),
    ]);

    let inserted = 0;
    for (const point of overall.data) {
      await upsertMetric(env, {
        projectId: project.id,
        source: "pypi",
        metric: "downloads",
        bucket: point.date,
        granularity: "day",
        unit: "downloads",
        value: point.downloads,
        asOf: collectedAt,
        meta: { category: point.category, package: recent.package },
      });
      inserted += 1;
    }

    const recentMetrics: Array<[string, number]> = [
      ["recent_last_day", recent.data.last_day],
      ["recent_last_week", recent.data.last_week],
      ["recent_last_month", recent.data.last_month],
    ];

    for (const [metric, value] of recentMetrics) {
      await upsertMetric(env, {
        projectId: project.id,
        source: "pypi",
        metric,
        bucket: collectedAt,
        granularity: "snapshot",
        unit: "downloads",
        value,
        asOf: collectedAt,
        meta: { package: recent.package },
      });
      inserted += 1;
    }

    return { status: "ok", inserted };
  } catch (error) {
    console.error(`[collect:pypi] ${project.id}: ${toMessage(error)}`);
    return { status: "error", detail: toMessage(error) };
  }
}

async function collectHomebrew(env: Env, project: ProjectConfig, collectedAt: string): Promise<CollectSourceResult> {
  if (!project.homebrewFormula) {
    return { status: "unavailable", detail: "No Homebrew formula configured for this project." };
  }

  try {
    const windows = [
      ["installs_30d", "30d"],
      ["installs_90d", "90d"],
      ["installs_365d", "365d"],
    ] as const;

    let inserted = 0;

    for (const [metric, window] of windows) {
      const payload = await withRetry(() => fetchJson<HomebrewAnalyticsResponse>(
        `https://formulae.brew.sh/api/analytics/install-on-request/${window}.json`,
      ));
      const targetFormula = normalizeFormula(project.homebrewFormula);
      const item = payload.items.find((candidate) => normalizeFormula(candidate.formula) === targetFormula);

      if (!item) {
        return {
          status: "unavailable",
          detail: `Formula '${project.homebrewFormula}' was not found in Homebrew analytics.`,
        };
      }

      await upsertMetric(env, {
        projectId: project.id,
        source: "homebrew",
        metric,
        bucket: collectedAt,
        granularity: "snapshot",
        unit: "installs",
        value: parseNumber(item.count),
        asOf: collectedAt,
        meta: {
          formula: item.formula,
          percent: item.percent,
          start_date: payload.start_date,
          end_date: payload.end_date,
        },
      });
      inserted += 1;
    }

    return { status: "ok", inserted };
  } catch (error) {
    console.error(`[collect:homebrew] ${project.id}: ${toMessage(error)}`);
    return { status: "error", detail: toMessage(error) };
  }
}

async function collectGitHub(env: Env, project: ProjectConfig, collectedAt: string): Promise<CollectSourceResult> {
  try {
    const repo = await withRetry(() => fetchJson<GitHubRepoResponse>(
      `https://api.github.com/repos/${project.githubOwner}/${project.githubRepo}`,
      {
        headers: githubHeaders(env),
      },
    ));

    const rows: MetricRow[] = [
      {
        projectId: project.id,
        source: "github",
        metric: "stars",
        bucket: collectedAt,
        granularity: "snapshot",
        unit: "stars",
        value: repo.stargazers_count,
        asOf: collectedAt,
        meta: { repo: repo.full_name, url: repo.html_url },
      },
      {
        projectId: project.id,
        source: "github",
        metric: "forks",
        bucket: collectedAt,
        granularity: "snapshot",
        unit: "forks",
        value: repo.forks_count,
        asOf: collectedAt,
        meta: { repo: repo.full_name, open_issues_count: repo.open_issues_count },
      },
    ];

    for (const row of rows) {
      await upsertMetric(env, row);
    }

    return { status: "ok", inserted: rows.length };
  } catch (error) {
    console.error(`[collect:github] ${project.id}: ${toMessage(error)}`);
    return { status: "error", detail: toMessage(error) };
  }
}

async function buildSummary(env: Env, project: ProjectConfig) {
  const rows = await env.METRICS_DB.prepare(`
    SELECT source, metric, value, unit, bucket, granularity, as_of, meta_json
    FROM (
      SELECT
        source,
        metric,
        value,
        unit,
        bucket,
        granularity,
        as_of,
        meta_json,
        ROW_NUMBER() OVER (
          PARTITION BY source, metric
          ORDER BY as_of DESC
        ) AS rank
      FROM metrics_snapshots
      WHERE project_id = ?1
    )
    WHERE rank = 1
    ORDER BY source, metric
  `)
    .bind(project.id)
    .all<{
      source: SourceName;
      metric: string;
      value: number;
      unit: string;
      bucket: string;
      granularity: Granularity;
      as_of: string;
      meta_json: string | null;
    }>();

  const latestRows = rows.results ?? [];
  const summary = {
    project: project.id,
    generatedAt: new Date().toISOString(),
    sources: {
      pypi: {
        status: "ok" as string,
        package: project.pypiPackage,
        metrics: {} as Record<string, unknown>,
      },
      homebrew: {
        status: project.homebrewFormula ? "ok" as string : "unavailable" as string,
        formula: project.homebrewFormula ?? null,
        metrics: {} as Record<string, unknown>,
        detail: project.homebrewFormula ? undefined : "No Homebrew formula configured for this project.",
      },
      github: {
        status: "ok" as string,
        repo: `${project.githubOwner}/${project.githubRepo}`,
        metrics: {} as Record<string, unknown>,
      },
    },
  };

  for (const row of latestRows) {
    const metric = {
      value: row.value,
      unit: row.unit,
      bucket: row.bucket,
      granularity: row.granularity,
      asOf: row.as_of,
      meta: parseJson(row.meta_json),
    };

    if (row.source === "pypi") {
      summary.sources.pypi.metrics[row.metric] = metric;
    } else if (row.source === "homebrew") {
      summary.sources.homebrew.metrics[row.metric] = metric;
    } else {
      summary.sources.github.metrics[row.metric] = metric;
    }
  }

  if (Object.keys(summary.sources.pypi.metrics).length === 0) {
    summary.sources.pypi.status = "error";
  }

  if (project.homebrewFormula && Object.keys(summary.sources.homebrew.metrics).length === 0) {
    summary.sources.homebrew.status = "error";
    summary.sources.homebrew.detail = "No Homebrew metrics have been collected yet.";
  }

  if (Object.keys(summary.sources.github.metrics).length === 0) {
    summary.sources.github.status = "error";
  }

  return summary;
}

async function buildTimeseries(env: Env, project: ProjectConfig, params: URLSearchParams) {
  const source = params.get("source");
  const metric = params.get("metric");
  const window = params.get("window") ?? "90d";
  const since = subtractDays(new Date(), parseWindow(window)).toISOString();
  const sinceDate = since.slice(0, 10);

  const filters: string[] = ["project_id = ?1"];
  const binds: Array<string> = [project.id];

  if (source) {
    filters.push(`source = ?${binds.length + 1}`);
    binds.push(source);
  }

  if (metric) {
    filters.push(`metric = ?${binds.length + 1}`);
    binds.push(metric);
  }

  filters.push(`(
    (granularity = 'day' AND bucket >= ?${binds.length + 1})
    OR
    (granularity = 'snapshot' AND as_of >= ?${binds.length + 2})
  )`);
  binds.push(sinceDate, since);

  const query = `
    SELECT source, metric, value, unit, bucket, granularity, as_of, meta_json
    FROM metrics_snapshots
    WHERE ${filters.join(" AND ")}
    ORDER BY source, metric, bucket ASC
  `;

  const rows = await env.METRICS_DB.prepare(query).bind(...binds).all<{
    source: SourceName;
    metric: string;
    value: number;
    unit: string;
    bucket: string;
    granularity: Granularity;
    as_of: string;
    meta_json: string | null;
  }>();

  const grouped = new Map<string, {
    source: SourceName;
    metric: string;
    unit: string;
    granularity: Granularity;
    points: Array<Record<string, unknown>>;
  }>();

  for (const row of rows.results ?? []) {
    const key = `${row.source}:${row.metric}`;
    if (!grouped.has(key)) {
      grouped.set(key, {
        source: row.source,
        metric: row.metric,
        unit: row.unit,
        granularity: row.granularity,
        points: [],
      });
    }

    grouped.get(key)?.points.push({
      bucket: row.bucket,
      asOf: row.as_of,
      value: row.value,
      meta: parseJson(row.meta_json),
    });
  }

  return {
    project: project.id,
    window,
    generatedAt: new Date().toISOString(),
    series: [...grouped.values()],
  };
}

async function upsertMetric(env: Env, row: MetricRow): Promise<void> {
  await env.METRICS_DB.prepare(`
    INSERT INTO metrics_snapshots (
      project_id,
      source,
      metric,
      bucket,
      granularity,
      unit,
      value,
      as_of,
      meta_json
    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
    ON CONFLICT(project_id, source, metric, bucket)
    DO UPDATE SET
      granularity = excluded.granularity,
      unit = excluded.unit,
      value = excluded.value,
      as_of = excluded.as_of,
      meta_json = excluded.meta_json
  `)
    .bind(
      row.projectId,
      row.source,
      row.metric,
      row.bucket,
      row.granularity,
      row.unit,
      row.value,
      row.asOf,
      row.meta ? JSON.stringify(row.meta) : null,
    )
    .run();
}

async function withRetry<T>(
  fn: () => Promise<T>,
  { retries = 1, delayMs = 2000 }: { retries?: number; delayMs?: number } = {},
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      if (attempt < retries) {
        console.warn(`[retry] Attempt ${attempt + 1} failed, retrying in ${delayMs}ms: ${toMessage(error)}`);
        await new Promise((resolve) => setTimeout(resolve, delayMs));
      }
    }
  }
  throw lastError;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      accept: "application/json",
      "user-agent": "opentraces-metrics-worker/0.1.0",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    throw new Error(`Fetch failed for ${url}: ${response.status} ${response.statusText}`);
  }

  return response.json<T>();
}

function githubHeaders(env: Env): HeadersInit {
  if (!env.GITHUB_TOKEN) {
    return {
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
    };
  }

  return {
    accept: "application/vnd.github+json",
    authorization: `Bearer ${env.GITHUB_TOKEN}`,
    "x-github-api-version": "2022-11-28",
  };
}

function parseWindow(window: string): number {
  const match = window.match(/^(\d+)d$/);
  if (!match) {
    return 90;
  }
  return Math.max(1, Number.parseInt(match[1], 10));
}

function subtractDays(date: Date, days: number): Date {
  const copy = new Date(date);
  copy.setUTCDate(copy.getUTCDate() - days);
  return copy;
}

function parseNumber(value: string): number {
  return Number.parseInt(value.replace(/,/g, ""), 10);
}

function normalizeFormula(value: string): string {
  return value.trim().toLowerCase();
}

function parseJson(value: string | null): Record<string, unknown> | null {
  if (!value) return null;

  try {
    return JSON.parse(value) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function toMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

function json(data: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(data, null, 2), {
    ...init,
    headers: {
      ...JSON_HEADERS,
      ...(init?.headers ?? {}),
    },
  });
}

function withCors(response: Response, env: Env): Response {
  const headers = new Headers(response.headers);
  headers.set("access-control-allow-origin", env.CORS_ORIGIN ?? "*");
  headers.set("access-control-allow-methods", "GET,POST,OPTIONS");
  headers.set("access-control-allow-headers", "authorization,content-type");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}
