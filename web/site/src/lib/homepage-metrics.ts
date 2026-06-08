import { type SummaryResponse } from "@/lib/distribution-metrics";

const HF_API = "https://huggingface.co/api";
const VIEWER = "https://datasets-server.huggingface.co";
const REVALIDATE_SECONDS = 300;

interface TimeseriesPoint {
  value: number;
}

interface TimeseriesSeries {
  points: TimeseriesPoint[];
}

interface TimeseriesResponse {
  series: TimeseriesSeries[];
}

interface HFDataset {
  id: string;
  tags?: string[];
  downloads?: number;
}

interface ViewerSplit {
  num_examples?: number;
}

interface ViewerConfig {
  splits?: Record<string, ViewerSplit>;
}

interface ViewerInfoResponse {
  dataset_info?: Record<string, ViewerConfig>;
}

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const response = await fetch(url, {
      next: { revalidate: REVALIDATE_SECONDS },
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

async function fetchText(url: string): Promise<string | null> {
  try {
    const response = await fetch(url, {
      next: { revalidate: REVALIDATE_SECONDS },
    });

    if (!response.ok) {
      return null;
    }

    return response.text();
  } catch {
    return null;
  }
}

function getStatsApiBaseUrl(): string | null {
  return process.env.STATS_API_BASE_URL ?? process.env.NEXT_PUBLIC_STATS_API ?? null;
}

function formatCompact(value: number | null): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-US", {
    notation: value >= 1000 ? "compact" : "standard",
    maximumFractionDigits: value >= 1000 ? 1 : 0,
  }).format(value);
}

function isOpentracesDataset(dataset: HFDataset): boolean {
  return Boolean(
    dataset.tags?.some((tag) => tag === "opentraces" || tag === "agent-traces")
      || dataset.id.toLowerCase().includes("opentraces"),
  );
}

async function getCommunityDatasets(): Promise<HFDataset[]> {
  const tagBase = "tags=opentraces&sort=downloads&direction=-1&limit=50&full=true";
  const searchBase = "sort=downloads&direction=-1&limit=50&full=true";

  const results = await Promise.allSettled([
    fetchJson<HFDataset[]>(`${HF_API}/datasets?${tagBase}`),
    fetchJson<HFDataset[]>(`${HF_API}/datasets?search=opentraces&${searchBase}`),
  ]);

  const seen = new Set<string>();
  const datasets: HFDataset[] = [];

  for (const result of results) {
    if (result.status !== "fulfilled" || !result.value) continue;

    for (const dataset of result.value) {
      if (seen.has(dataset.id)) continue;
      if (!isOpentracesDataset(dataset)) continue;
      seen.add(dataset.id);
      datasets.push(dataset);
    }
  }

  return datasets;
}

async function getReadmeTraceCount(repoId: string): Promise<number> {
  const text = await fetchText(`https://huggingface.co/datasets/${repoId}/resolve/main/README.md`);
  if (!text) return 0;

  const match = text.match(/\|\s*Total traces\s*\|\s*([\d,]+)\s*\|/i);
  return match ? Number.parseInt(match[1].replace(/,/g, ""), 10) : 0;
}

async function getDatasetTraceCount(repoId: string): Promise<number> {
  const info = await fetchJson<ViewerInfoResponse>(`${VIEWER}/info?dataset=${encodeURIComponent(repoId)}`);
  const datasetInfo = info?.dataset_info ?? {};

  const serverCount = Object.values(datasetInfo).reduce((total, config) => {
    const splits = config?.splits ?? {};
    return total + Object.values(splits).reduce((sum, split) => sum + (split?.num_examples ?? 0), 0);
  }, 0);

  if (serverCount > 0) {
    return serverCount;
  }

  return getReadmeTraceCount(repoId);
}

async function getCommunityDatasetStats(): Promise<{ totalTraces: number | null; totalDownloads: number }> {
  const datasets = await getCommunityDatasets();
  if (!datasets.length) return { totalTraces: null, totalDownloads: 0 };

  const totalDownloads = datasets.reduce((sum, ds) => sum + (ds.downloads ?? 0), 0);
  const counts = await Promise.all(datasets.map((dataset) => getDatasetTraceCount(dataset.id)));
  const totalTraces = counts.reduce((sum, count) => sum + count, 0);

  return { totalTraces, totalDownloads };
}

async function getDistributionSummary(baseUrl: string): Promise<SummaryResponse | null> {
  return fetchJson<SummaryResponse>(`${baseUrl}/v1/projects/opentraces/summary`);
}

async function getPypiAllTimeDownloads(baseUrl: string): Promise<number | null> {
  const response = await fetchJson<TimeseriesResponse>(
    `${baseUrl}/v1/projects/opentraces/timeseries?source=pypi&metric=downloads&window=3650d`,
  );

  const points = response?.series[0]?.points ?? [];
  if (!points.length) return null;

  return points.reduce((sum, point) => sum + point.value, 0);
}

// Recent daily install volume, for the unlabeled hero sparkline. Returns the
// raw per-day values over the window (newest last); the sparkline normalizes
// them, so absolute magnitude and units are intentionally not surfaced.
async function getInstallSeries(baseUrl: string): Promise<number[]> {
  const response = await fetchJson<TimeseriesResponse>(
    `${baseUrl}/v1/projects/opentraces/timeseries?source=pypi&metric=downloads&window=90d`,
  );

  return (response?.series[0]?.points ?? []).map((point) => point.value);
}

export interface HeroMetricItem {
  icon: "install" | "download" | "trace";
  label: string;
  value: string;
  title: string;
}

export interface HomepageHeroData {
  metrics: HeroMetricItem[];
  stars: string;
  installSeries: number[];
}

// Star count on its own, formatted exactly like the homepage badge ("—" when
// unavailable). Used by /api/stars so the nav badge is identical on every page.
export async function getGithubStars(): Promise<string> {
  const baseUrl = getStatsApiBaseUrl();
  const summary = baseUrl ? await getDistributionSummary(baseUrl) : null;
  const githubStars = summary?.sources.github.metrics.stars?.value ?? null;
  return formatCompact(githubStars);
}

export async function getHomepageHeroMetrics(): Promise<HomepageHeroData> {
  const baseUrl = getStatsApiBaseUrl();

  const [summary, pypiAllTimeDownloads, communityStats, installSeries] = await Promise.all([
    baseUrl ? getDistributionSummary(baseUrl) : Promise.resolve(null),
    baseUrl ? getPypiAllTimeDownloads(baseUrl) : Promise.resolve(null),
    getCommunityDatasetStats(),
    baseUrl ? getInstallSeries(baseUrl) : Promise.resolve([]),
  ]);

  const brewInstalls365d = summary?.sources.homebrew.metrics.installs_365d?.value ?? 0;
  const githubStars = summary?.sources.github.metrics.stars?.value ?? null;
  const combinedInstalls = pypiAllTimeDownloads == null ? null : pypiAllTimeDownloads + brewInstalls365d;

  return {
    metrics: [
      {
        icon: "install",
        label: "installs",
        value: formatCompact(combinedInstalls),
        title: "PyPI cumulative history plus the latest Homebrew 365d window.",
      },
      {
        icon: "download",
        label: "downloads",
        value: formatCompact(communityStats.totalDownloads || null),
        title: "Total dataset downloads across all community HuggingFace datasets.",
      },
      {
        icon: "trace",
        label: "traces",
        value: formatCompact(communityStats.totalTraces),
        title: "Community trace total using the same Hugging Face explorer sources as /explorer.",
      },
    ],
    stars: formatCompact(githubStars),
    installSeries,
  };
}
