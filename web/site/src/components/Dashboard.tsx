"use client";

import { useEffect, useState, useCallback, useRef, FormEvent } from "react";

const HF_API = "https://huggingface.co/api";
const VIEWER = "https://datasets-server.huggingface.co";
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

interface CacheEntry {
  datasetStats: DatasetStats[];
  sampleRows: TraceRow[];
  ts: number;
}

function cacheKey(query: string, isUser: boolean) {
  return `ot:explorer:${isUser ? "u" : "c"}:${query}`;
}

function readCache(key: string): CacheEntry | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const entry: CacheEntry = JSON.parse(raw);
    if (Date.now() - entry.ts > CACHE_TTL) {
      sessionStorage.removeItem(key);
      return null;
    }
    return entry;
  } catch { return null; }
}

function writeCache(key: string, datasetStats: DatasetStats[], sampleRows: TraceRow[]) {
  try {
    sessionStorage.setItem(key, JSON.stringify({ datasetStats, sampleRows, ts: Date.now() }));
  } catch { /* quota exceeded, ignore */ }
}

interface HFDataset {
  id: string;
  author: string;
  lastModified: string;
  downloads: number;
  likes: number;
  tags: string[];
}

interface DatasetStats {
  repoId: string;
  author: string;
  numTraces: number | null; // null = still loading
  downloads: number;
  lastModified: string;
}

interface TraceRow {
  [key: string]: unknown;
  agent?: string | { name?: string; model?: string };
  model?: string;
  metrics?: { total_steps?: number; estimated_cost_usd?: number; total_input_tokens?: number; total_output_tokens?: number; cache_hit_rate?: number; total_duration_s?: number };
  outcome?: { success?: boolean; committed?: boolean } | boolean;
  security?: { tier?: string } | string;
  dependencies?: string[];
  timestamp_start?: string;
  steps?: unknown[];
}

function getAgent(r: TraceRow): string {
  if (typeof r.agent === "string") return r.agent;
  if (typeof r.agent === "object" && r.agent) return r.agent.name || "-";
  return "-";
}

function getModel(r: TraceRow): string {
  if (r.model && typeof r.model === "string") return r.model;
  if (typeof r.agent === "object" && r.agent) return r.agent.model || "-";
  return "-";
}

function getSuccess(r: TraceRow): boolean | null {
  if (typeof r.outcome === "boolean") return r.outcome;
  if (typeof r.outcome === "object" && r.outcome) return r.outcome.success ?? null;
  return null;
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
}

function Skeleton({ width = "60%", height = 16 }: { width?: string | number; height?: number }) {
  return (
    <span
      className="skeleton-pulse"
      style={{ display: "inline-block", width, height, borderRadius: 3, verticalAlign: "middle" }}
    />
  );
}

export default function Dashboard() {
  const [mode, setMode] = useState<"community" | "user">("community");
  const [username, setUsername] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [datasetStats, setDatasetStats] = useState<DatasetStats[]>([]);
  const [sampleRows, setSampleRows] = useState<TraceRow[]>([]);
  const [phase, setPhase] = useState<"loading" | "datasets" | "enriching" | "done">("loading");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetchDatasets = useCallback(async (query: string, isUser: boolean) => {
    const key = cacheKey(query, isUser);
    const cached = readCache(key);

    if (cached) {
      setDatasetStats(cached.datasetStats);
      setSampleRows(cached.sampleRows);
      setPhase("done");
      setRefreshing(true);
    } else {
      setPhase("loading");
      setError(null);
      setDatasetStats([]);
      setSampleRows([]);
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const sig = controller.signal;

    try {
      const tagBase = "tags=opentraces&sort=downloads&direction=-1&limit=50&full=true";
      const searchBase = "sort=downloads&direction=-1&limit=50&full=true";

      const urls: string[] = [];
      if (isUser) {
        urls.push(`${HF_API}/datasets?${tagBase}&author=${encodeURIComponent(query)}`);
        urls.push(`${HF_API}/datasets?search=${encodeURIComponent(query)}/opentraces&${searchBase}`);
        urls.push(`${HF_API}/datasets?author=${encodeURIComponent(query)}&search=opentraces&${searchBase}`);
      } else {
        urls.push(`${HF_API}/datasets?${tagBase}`);
        urls.push(`${HF_API}/datasets?search=opentraces&${searchBase}`);
      }

      const results = await Promise.allSettled(urls.map(u => fetch(u, { signal: sig }).then(r => r.ok ? r.json() : [])));
      if (sig.aborted) return;

      const allData: HFDataset[] = [];
      const seen = new Set<string>();
      for (const r of results) {
        if (r.status === "fulfilled") {
          for (const d of r.value as HFDataset[]) {
            if (!seen.has(d.id)) {
              seen.add(d.id);
              allData.push(d);
            }
          }
        }
      }

      const filtered = allData.filter(d =>
        d.tags?.some(t => t === "opentraces" || t === "agent-traces") ||
        d.id.toLowerCase().includes("opentraces")
      );

      // Phase 2: show dataset table immediately with placeholder trace counts
      const initial: DatasetStats[] = filtered.map(ds => ({
        repoId: ds.id,
        author: ds.author,
        numTraces: null, // loading
        downloads: ds.downloads,
        lastModified: ds.lastModified,
      }));
      setDatasetStats(initial);
      setPhase("enriching");

      // Fire /info requests + sample rows in parallel, stream results as they arrive
      const enriched = [...initial];
      let bestRepoId = filtered.length > 0 ? filtered[0].id : null;
      let bestCount = 0;

      // Start sample rows fetch early (from first dataset, update if a bigger one is found)
      const rowsFetchRef: { promise: Promise<TraceRow[]> | null } = { promise: null };
      if (bestRepoId) {
        rowsFetchRef.promise = fetch(
          `${VIEWER}/rows?dataset=${encodeURIComponent(bestRepoId)}&config=default&split=train&offset=0&length=100`,
          { signal: sig }
        ).then(r => r.ok ? r.json() : null)
         .then(d => d?.rows?.map((r: { row: TraceRow }) => r.row) || [])
         .catch(() => []);
      }

      // Stream /info results as they complete
      const infoPromises = filtered.map(async (ds, idx) => {
        try {
          const r = await fetch(`${VIEWER}/info?dataset=${encodeURIComponent(ds.id)}`, { signal: sig });
          if (!r.ok) return;
          const info = await r.json();
          const num = info?.dataset_info?.default?.splits?.train?.num_examples ?? 0;
          enriched[idx] = { ...enriched[idx], numTraces: num };
          if (num > bestCount) {
            bestCount = num;
            bestRepoId = ds.id;
          }
          // Update state progressively
          if (!sig.aborted) {
            setDatasetStats([...enriched].sort((a, b) => (b.numTraces ?? 0) - (a.numTraces ?? 0)));
          }
        } catch {
          enriched[idx] = { ...enriched[idx], numTraces: 0 };
          if (!sig.aborted) {
            setDatasetStats([...enriched].sort((a, b) => (b.numTraces ?? 0) - (a.numTraces ?? 0)));
          }
        }
      });

      await Promise.allSettled(infoPromises);
      if (sig.aborted) return;

      // If a bigger dataset was found, fetch its rows instead
      const finalStats = [...enriched].sort((a, b) => (b.numTraces ?? 0) - (a.numTraces ?? 0));
      setDatasetStats(finalStats);

      let rows: TraceRow[] = [];
      if (bestRepoId && bestCount > 0 && bestRepoId !== filtered[0]?.id) {
        // A different dataset turned out to be biggest, fetch its rows
        try {
          const rowsRes = await fetch(
            `${VIEWER}/rows?dataset=${encodeURIComponent(bestRepoId)}&config=default&split=train&offset=0&length=100`,
            { signal: sig }
          );
          if (rowsRes.ok) {
            const rowsData = await rowsRes.json();
            rows = rowsData.rows?.map((r: { row: TraceRow }) => r.row) || [];
          }
        } catch { /* ignore */ }
      } else if (rowsFetchRef.promise) {
        rows = await rowsFetchRef.promise;
      }

      if (sig.aborted) return;
      setSampleRows(rows);
      writeCache(key, finalStats, rows);
    } catch (e) {
      if (sig.aborted) return;
      setError(e instanceof Error ? e.message : "Failed to fetch");
    } finally {
      if (!sig.aborted) {
        setPhase("done");
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    fetchDatasets("opentraces", false);
  }, [fetchDatasets]);

  function handleUserSearch(e: FormEvent) {
    e.preventDefault();
    if (!searchInput.trim()) return;
    setUsername(searchInput.trim());
    setMode("user");
    fetchDatasets(searchInput.trim(), true);
  }

  function switchToCommunity() {
    setMode("community");
    setUsername("");
    setSearchInput("");
    fetchDatasets("opentraces", false);
  }

  const isLoading = phase === "loading";
  const showStructure = phase !== "loading" || datasetStats.length > 0;

  // Compute stats
  const totalTraces = datasetStats.reduce((s, d) => s + (d.numTraces ?? 0), 0);
  const totalDownloads = datasetStats.reduce((s, d) => s + d.downloads, 0);
  const contributors = new Set(datasetStats.map(d => d.author)).size;
  const datasetCount = datasetStats.length;
  const statsReady = phase === "done" || (phase === "enriching" && datasetStats.some(d => d.numTraces !== null));

  // Stats from sample rows
  const agents: Record<string, number> = {};
  const models: Record<string, number> = {};
  const deps: Record<string, number> = {};
  let totalSteps = 0;
  let totalCost = 0;
  let totalTokens = 0;
  let successCount = 0;
  let outcomeCount = 0;
  let costCount = 0;

  for (const r of sampleRows) {
    const a = getAgent(r);
    if (a !== "-") agents[a] = (agents[a] || 0) + 1;
    const m = getModel(r);
    if (m !== "-") models[m] = (models[m] || 0) + 1;
    if (r.dependencies && Array.isArray(r.dependencies)) {
      for (const d of r.dependencies) deps[d] = (deps[d] || 0) + 1;
    }
    if (r.metrics?.total_steps) totalSteps += r.metrics.total_steps;
    if (r.metrics?.estimated_cost_usd) { totalCost += r.metrics.estimated_cost_usd; costCount++; }
    if (r.metrics?.total_input_tokens) totalTokens += r.metrics.total_input_tokens;
    if (r.metrics?.total_output_tokens) totalTokens += r.metrics.total_output_tokens;
    const s = getSuccess(r);
    if (s !== null) { outcomeCount++; if (s) successCount++; }
  }

  const avgCost = costCount > 0 ? (totalCost / costCount).toFixed(2) : null;
  const avgSteps = sampleRows.length > 0 ? Math.round(totalSteps / sampleRows.length) : 0;
  const successRate = outcomeCount > 0 ? Math.round((successCount / outcomeCount) * 100) : null;
  const sortedAgents = Object.entries(agents).sort((a, b) => b[1] - a[1]);
  const sortedModels = Object.entries(models).sort((a, b) => b[1] - a[1]);
  const sortedDeps = Object.entries(deps).sort((a, b) => b[1] - a[1]).slice(0, 10);

  return (
    <section style={{ padding: "48px 0" }}>
      {/* Search bar */}
      <div className="explorer-search">
        <div className="section-title" style={{ margin: 0 }}>Explorer</div>
        <div style={{ flex: 1 }} />
        <form onSubmit={handleUserSearch} style={{ display: "flex", gap: 0 }}>
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="HF username..."
            className="explorer-search-input"
          />
          <button type="submit" style={{
            fontFamily: "var(--font-mono)", fontSize: 11, padding: "8px 16px",
            border: "1px solid var(--border)", background: "var(--surface)",
            color: "var(--text-secondary)", cursor: "pointer",
          }}>
            search
          </button>
        </form>
        {mode === "user" && (
          <button onClick={switchToCommunity} style={{
            fontFamily: "var(--font-mono)", fontSize: 11, padding: "8px 12px",
            border: "1px solid var(--border)", background: "transparent",
            color: "var(--text-muted)", cursor: "pointer",
          }}>
            community view
          </button>
        )}
      </div>

      {/* Context line */}
      <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 24, fontFamily: "var(--font-body)" }}>
        {mode === "user"
          ? `Showing datasets for ${username}.`
          : "All public opentraces datasets on Hugging Face Hub."}
        {(isLoading || refreshing || phase === "enriching") && (
          <span style={{ marginLeft: 8 }}>
            <svg width="14" height="14" viewBox="0 0 14 14" style={{ verticalAlign: "middle", animation: "spin 1s linear infinite" }}>
              <circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="20 12" />
            </svg>
            {isLoading ? " Loading..." : refreshing ? " Updating..." : " Enriching..."}
          </span>
        )}
        {error && <span style={{ color: "var(--red)" }}> {error}</span>}
      </p>

      {/* Top-line stats */}
      {showStructure && (
        <div className="stats-row" style={{ marginBottom: 32 }}>
          <div className="stat-cell">
            <div className="stat-label">total traces</div>
            <div className="stat-value">{statsReady ? fmt(totalTraces) : <Skeleton width={48} height={28} />}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-label">datasets</div>
            <div className="stat-value">{statsReady ? datasetCount : <Skeleton width={32} height={28} />}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-label">contributors</div>
            <div className="stat-value">{statsReady ? contributors : <Skeleton width={32} height={28} />}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-label">downloads</div>
            <div className="stat-value">{statsReady ? fmt(totalDownloads) : <Skeleton width={48} height={28} />}</div>
          </div>
        </div>
      )}

      {/* Computed insights from sample data */}
      {sampleRows.length > 0 && (
        <>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 16 }}>
            insights (sampled from {sampleRows.length} traces)
          </div>

          <div className="insights-grid">
            {/* Efficiency */}
            <div style={{ border: "1px solid var(--border)", padding: 20, background: "var(--surface)" }}>
              <div className="stat-label" style={{ marginBottom: 16 }}>efficiency</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {avgSteps > 0 && (
                  <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                    <span style={{ color: "var(--text-muted)" }}>avg steps/session</span>
                    <span style={{ color: "var(--text)" }}>{avgSteps}</span>
                  </div>
                )}
                {avgCost && (
                  <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                    <span style={{ color: "var(--text-muted)" }}>avg cost/session</span>
                    <span style={{ color: "var(--text)" }}>${avgCost}</span>
                  </div>
                )}
                {totalTokens > 0 && (
                  <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                    <span style={{ color: "var(--text-muted)" }}>total tokens</span>
                    <span style={{ color: "var(--text)" }}>{fmt(totalTokens)}</span>
                  </div>
                )}
                {successRate !== null && (
                  <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0" }}>
                    <span style={{ color: "var(--text-muted)" }}>success rate</span>
                    <span style={{ color: successRate >= 70 ? "var(--green)" : successRate >= 40 ? "var(--yellow)" : "var(--red)" }}>{successRate}%</span>
                  </div>
                )}
                {avgSteps === 0 && !avgCost && totalTokens === 0 && successRate === null && (
                  <div style={{ color: "var(--text-dim)", fontSize: 11 }}>No metrics in this dataset schema</div>
                )}
              </div>
            </div>

            {/* Agent & Model breakdown */}
            <div style={{ border: "1px solid var(--border)", padding: 20, background: "var(--surface)" }}>
              <div className="stat-label" style={{ marginBottom: 16 }}>agents & models</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {sortedAgents.length > 0 && (
                  <>
                    <div style={{ fontSize: 9, color: "var(--text-dim)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 6 }}>agents</div>
                    {sortedAgents.slice(0, 4).map(([name, count]) => (
                      <div key={name} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", color: "var(--text-muted)" }}>
                        <span>{name}</span>
                        <span style={{ color: "var(--text-dim)" }}>{Math.round((count / sampleRows.length) * 100)}%</span>
                      </div>
                    ))}
                  </>
                )}
                {sortedModels.length > 0 && (
                  <>
                    <div style={{ fontSize: 9, color: "var(--text-dim)", letterSpacing: "0.1em", textTransform: "uppercase", marginTop: 12, marginBottom: 6 }}>models</div>
                    {sortedModels.slice(0, 4).map(([name, count]) => (
                      <div key={name} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", color: "var(--text-muted)" }}>
                        <span style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
                        <span style={{ color: "var(--text-dim)" }}>{Math.round((count / sampleRows.length) * 100)}%</span>
                      </div>
                    ))}
                  </>
                )}
                {sortedAgents.length === 0 && sortedModels.length === 0 && (
                  <div style={{ color: "var(--text-dim)", fontSize: 11 }}>No agent/model data</div>
                )}
              </div>
            </div>

            {/* Dependencies */}
            <div style={{ border: "1px solid var(--border)", padding: 20, background: "var(--surface)" }}>
              <div className="stat-label" style={{ marginBottom: 16 }}>top dependencies</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {sortedDeps.length > 0 ? sortedDeps.map(([dep, count]) => (
                  <div key={dep} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", color: "var(--text-muted)" }}>
                    <span>{dep}</span>
                    <span style={{ color: "var(--text-dim)" }}>{count}</span>
                  </div>
                )) : (
                  <div style={{ color: "var(--text-dim)", fontSize: 11 }}>No dependency data</div>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {/* Skeleton insights while enriching */}
      {phase === "enriching" && sampleRows.length === 0 && (
        <>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 16 }}>
            <Skeleton width={200} height={12} />
          </div>
          <div className="insights-grid">
            {[0, 1, 2].map(i => (
              <div key={i} style={{ border: "1px solid var(--border)", padding: 20, background: "var(--surface)" }}>
                <div className="stat-label" style={{ marginBottom: 16 }}><Skeleton width={80} height={12} /></div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <Skeleton width="90%" height={14} />
                  <Skeleton width="75%" height={14} />
                  <Skeleton width="60%" height={14} />
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Dataset list */}
      {showStructure && datasetStats.length > 0 && (
        <div className="tbl-wrap">
          <div className="tbl-head">
            <span className="tbl-title">datasets</span>
            <span style={{ fontSize: 10, color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>
              {datasetStats.length} found
            </span>
          </div>
          <table>
            <thead>
              <tr>
                <th>dataset</th>
                <th>contributor</th>
                <th>traces</th>
                <th>downloads</th>
                <th>last updated</th>
              </tr>
            </thead>
            <tbody>
              {datasetStats.map((d) => (
                <tr key={d.repoId}>
                  <td>
                    <a href={`https://huggingface.co/datasets/${d.repoId}`} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)", textDecoration: "none" }}>
                      {d.repoId}
                    </a>
                  </td>
                  <td>{d.author}</td>
                  <td>{d.numTraces === null ? <Skeleton width={32} height={14} /> : d.numTraces > 0 ? fmt(d.numTraces) : "-"}</td>
                  <td>{fmt(d.downloads)}</td>
                  <td style={{ color: "var(--text-dim)" }}>{new Date(d.lastModified).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Skeleton table while initial loading */}
      {isLoading && datasetStats.length === 0 && (
        <div className="tbl-wrap">
          <div className="tbl-head">
            <span className="tbl-title">datasets</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>dataset</th>
                <th>contributor</th>
                <th>traces</th>
                <th>downloads</th>
                <th>last updated</th>
              </tr>
            </thead>
            <tbody>
              {[0, 1, 2, 3].map(i => (
                <tr key={i}>
                  <td><Skeleton width="80%" height={14} /></td>
                  <td><Skeleton width="60%" height={14} /></td>
                  <td><Skeleton width={32} height={14} /></td>
                  <td><Skeleton width={40} height={14} /></td>
                  <td><Skeleton width={70} height={14} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Empty state */}
      {phase === "done" && datasetStats.length === 0 && !error && (
        <div style={{ textAlign: "center", padding: "48px 0", color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
          {mode === "user"
            ? `No opentraces datasets found for "${username}".`
            : "No opentraces datasets found yet. Be the first!"}
          <br />
          <code style={{ color: "var(--accent)", marginTop: 8, display: "inline-block" }}>pipx install opentraces && opentraces init</code>
        </div>
      )}
    </section>
  );
}
