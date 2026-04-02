export interface MetricValue {
  value: number;
  unit: string;
  bucket: string;
  granularity: "day" | "snapshot";
  asOf: string;
  meta: Record<string, unknown> | null;
}

export interface SummarySource {
  status: "ok" | "unavailable" | "error";
  metrics: Record<string, MetricValue>;
  detail?: string;
}

export interface SummaryResponse {
  project: string;
  generatedAt: string;
  sources: {
    pypi: SummarySource & { package: string };
    homebrew: SummarySource & { formula: string | null };
    github: SummarySource & { repo: string };
  };
}

export interface SeriesPoint {
  bucket: string;
  asOf: string;
  value: number;
  meta: Record<string, unknown> | null;
}

export interface TimeseriesItem {
  source: "pypi" | "homebrew" | "github";
  metric: string;
  unit: string;
  granularity: "day" | "snapshot";
  points: SeriesPoint[];
}

export interface TimeseriesResponse {
  project: string;
  window: string;
  generatedAt: string;
  series: TimeseriesItem[];
}

function getBaseUrl(): string | null {
  return process.env.STATS_API_BASE_URL ?? process.env.NEXT_PUBLIC_STATS_API ?? null;
}

async function fetchJson<T>(path: string): Promise<T | null> {
  const baseUrl = getBaseUrl();
  if (!baseUrl) return null;

  try {
    const response = await fetch(`${baseUrl}${path}`, {
      next: { revalidate: 300 },
      headers: {
        accept: "application/json",
      },
    });

    if (!response.ok) {
      return null;
    }

    return response.json() as Promise<T>;
  } catch {
    return null;
  }
}

export async function getDistributionMetrics(projectId = "opentraces"): Promise<{
  baseUrl: string | null;
  summary: SummaryResponse | null;
  pypiDownloads: TimeseriesResponse | null;
}> {
  const baseUrl = getBaseUrl();
  const [summary, pypiDownloads] = await Promise.all([
    fetchJson<SummaryResponse>(`/v1/projects/${projectId}/summary`),
    fetchJson<TimeseriesResponse>(`/v1/projects/${projectId}/timeseries?source=pypi&metric=downloads&window=30d`),
  ]);

  return {
    baseUrl,
    summary,
    pypiDownloads,
  };
}
