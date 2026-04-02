import { getDistributionMetrics, type MetricValue, type SummarySource, type TimeseriesItem } from "@/lib/distribution-metrics";
import SectionRule from "./SectionRule";

function fmtCompact(value: number | null | undefined): string {
  if (value == null) return "\u2014";
  return new Intl.NumberFormat("en-US", {
    notation: value >= 1000 ? "compact" : "standard",
    maximumFractionDigits: value >= 1000 ? 1 : 0,
  }).format(value);
}

function fmtDate(value: string | null | undefined): string {
  if (!value) return "\u2014";
  return new Intl.DateTimeFormat("en-GB", {
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

function getMetric(source: SummarySource, key: string): MetricValue | null {
  return source.metrics[key] ?? null;
}

function getStatusClass(status: SummarySource["status"]): string {
  if (status === "ok") return "badge-ok";
  if (status === "unavailable") return "badge-wa";
  return "badge-er";
}

function Sparkline({ series }: { series: TimeseriesItem | null }) {
  const points = series?.points.slice(-14) ?? [];
  if (points.length < 2) {
    return <div className="metrics-empty">waiting for enough daily points to draw a line</div>;
  }

  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = 560;
  const height = 140;
  const step = width / Math.max(1, points.length - 1);

  const polyline = points.map((point, index) => {
    const x = index * step;
    const normalized = max === min ? 0.5 : (point.value - min) / (max - min);
    const y = height - normalized * (height - 16) - 8;
    return `${x},${y}`;
  }).join(" ");

  const latest = points.at(-1);
  const previous = points.at(-2);
  const delta = latest && previous ? latest.value - previous.value : null;

  return (
    <div>
      <div className="metrics-chart-head">
        <div>
          <div className="stat-label" style={{ marginBottom: 4 }}>daily downloads</div>
          <div className="metrics-big-number">{latest ? fmtCompact(latest.value) : "\u2014"}</div>
        </div>
        <div className={`badge ${delta != null && delta >= 0 ? "badge-ok" : "badge-er"}`}>
          {delta == null ? "n/a" : `${delta >= 0 ? "+" : ""}${fmtCompact(delta)}`}
        </div>
      </div>
      <svg className="metrics-sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="PyPI daily downloads for the last 14 days">
        <polyline points={polyline} fill="none" stroke="var(--accent)" strokeWidth="3" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="metrics-axis">
        <span>{fmtDate(points[0]?.bucket)}</span>
        <span>{fmtDate(points.at(-1)?.bucket)}</span>
      </div>
    </div>
  );
}

export default async function Stats() {
  const { baseUrl, summary, pypiDownloads } = await getDistributionMetrics("opentraces");

  if (!baseUrl || !summary) {
    return (
      <section>
        <SectionRule label="distribution" />
        <div className="tbl-wrap" style={{ marginBottom: 16 }}>
          <div className="tbl-head">
            <span className="tbl-title">live package metrics</span>
            <span className="badge badge-wa">pending api</span>
          </div>
          <div className="metrics-empty-state">
            <p>
              Configure <code>STATS_API_BASE_URL</code> or <code>NEXT_PUBLIC_STATS_API</code> to render
              live PyPI, Homebrew, and GitHub stats from the Cloudflare Worker.
            </p>
            <p className="metrics-muted">
              The site is wired for <code>/v1/projects/opentraces/summary</code> and{" "}
              <code>/v1/projects/opentraces/timeseries?source=pypi&amp;metric=downloads&amp;window=30d</code>.
            </p>
          </div>
        </div>
      </section>
    );
  }

  const pypiLastDay = getMetric(summary.sources.pypi, "recent_last_day");
  const pypiLastMonth = getMetric(summary.sources.pypi, "recent_last_month");
  const githubStars = getMetric(summary.sources.github, "stars");
  const homebrew30d = getMetric(summary.sources.homebrew, "installs_30d");
  const homebrew365d = getMetric(summary.sources.homebrew, "installs_365d");
  const pypiSeries = pypiDownloads?.series.find((series) => series.source === "pypi" && series.metric === "downloads") ?? null;

  const cards = [
    { label: "pypi / day", value: fmtCompact(pypiLastDay?.value), delta: pypiLastDay ? `as of ${fmtDate(pypiLastDay.asOf)}` : "waiting for fetch", dir: "up" as const },
    { label: "pypi / 30d", value: fmtCompact(pypiLastMonth?.value), delta: "official pypistats recent window", dir: "up" as const },
    { label: "github stars", value: fmtCompact(githubStars?.value), delta: githubStars ? "simple repo snapshot" : "waiting for snapshot", dir: "up" as const },
    { label: "brew / 30d", value: fmtCompact(homebrew30d?.value), delta: homebrew30d ? "install-on-request window" : (summary.sources.homebrew.detail ?? "not in feed yet"), dir: homebrew30d ? "up" as const : "dn" as const },
    { label: "brew / 365d", value: fmtCompact(homebrew365d?.value), delta: homebrew365d ? "rolling official aggregate" : "public analytics unavailable", dir: homebrew365d ? "up" as const : "dn" as const },
  ];

  return (
    <section>
      <SectionRule label="distribution" />

      <div style={{ marginBottom: 16, color: "var(--text-muted)", maxWidth: 760 }}>
        Public package and repo signals, split into two API shapes: summary numbers for cards and time series for charts.
      </div>

      <div className="stats-row" style={{ marginBottom: 16 }}>
        {cards.map((card) => (
          <div key={card.label} className="stat-cell">
            <div className="stat-label">{card.label}</div>
            <div className="stat-value">{card.value}</div>
            <div className={`stat-delta ${card.dir}`}>{card.delta}</div>
          </div>
        ))}
      </div>

      <div className="metrics-grid">
        <div className="tbl-wrap">
          <div className="tbl-head">
            <span className="tbl-title">pypi daily downloads</span>
            <span className="badge badge-ac">timeseries</span>
          </div>
          <div className="metrics-panel">
            <Sparkline series={pypiSeries} />
            <p className="metrics-muted">
              PyPI is the strongest public daily signal right now. This line uses the Worker&apos;s
              <code> /timeseries</code> endpoint and reflects daily download buckets.
            </p>
          </div>
        </div>

        <div className="tbl-wrap">
          <div className="tbl-head">
            <span className="tbl-title">channel status</span>
            <span className="badge badge-bl">summary</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>source</th>
                <th>status</th>
                <th>meaning</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>PyPI</td>
                <td><span className={`badge ${getStatusClass(summary.sources.pypi.status)}`}>{summary.sources.pypi.status}</span></td>
                <td>true daily download points</td>
              </tr>
              <tr>
                <td>GitHub</td>
                <td><span className={`badge ${getStatusClass(summary.sources.github.status)}`}>{summary.sources.github.status}</span></td>
                <td>current repo star count, snapshot-based history</td>
              </tr>
              <tr>
                <td>Homebrew</td>
                <td><span className={`badge ${getStatusClass(summary.sources.homebrew.status)}`}>{summary.sources.homebrew.status}</span></td>
                <td>official rolling windows, not true daily installs</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
