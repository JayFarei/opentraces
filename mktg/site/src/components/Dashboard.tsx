"use client";

import { useEffect, useState, useCallback, FormEvent } from "react";

const HF_API = "https://huggingface.co/api";
const VIEWER = "https://datasets-server.huggingface.co";

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
  numTraces: number;
  downloads: number;
  lastModified: string;
}

interface TraceRow {
  // Flexible to handle different schemas
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

export default function Dashboard() {
  const [mode, setMode] = useState<"community" | "user">("community");
  const [username, setUsername] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [datasets, setDatasets] = useState<HFDataset[]>([]);
  const [datasetStats, setDatasetStats] = useState<DatasetStats[]>([]);
  const [sampleRows, setSampleRows] = useState<TraceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch datasets
  const fetchDatasets = useCallback(async (query: string, isUser: boolean) => {
    setLoading(true);
    setError(null);
    setDatasets([]);
    setDatasetStats([]);
    setSampleRows([]);

    try {
      const searchParam = isUser ? `author=${encodeURIComponent(query)}&search=opentraces` : "search=opentraces";
      const res = await fetch(`${HF_API}/datasets?${searchParam}&sort=downloads&direction=-1&limit=50`);
      if (!res.ok) throw new Error(`HF API ${res.status}`);
      const data: HFDataset[] = await res.json();

      // For user mode, also search by username prefix in dataset name
      let filtered = data;
      if (isUser) {
        const userRes = await fetch(`${HF_API}/datasets?search=${encodeURIComponent(query)}/opentraces&sort=downloads&direction=-1&limit=50`);
        if (userRes.ok) {
          const userData: HFDataset[] = await userRes.json();
          const ids = new Set(filtered.map(d => d.id));
          for (const d of userData) {
            if (!ids.has(d.id)) filtered.push(d);
          }
        }
      }

      setDatasets(filtered);

      // Fan out /info requests
      const infos = await Promise.allSettled(
        filtered.map(async (ds) => {
          try {
            const r = await fetch(`${VIEWER}/info?dataset=${encodeURIComponent(ds.id)}`);
            if (!r.ok) return { repoId: ds.id, author: ds.author, numTraces: 0, downloads: ds.downloads, lastModified: ds.lastModified };
            const info = await r.json();
            const num = info?.dataset_info?.default?.splits?.train?.num_examples ?? 0;
            return { repoId: ds.id, author: ds.author, numTraces: num, downloads: ds.downloads, lastModified: ds.lastModified };
          } catch {
            return { repoId: ds.id, author: ds.author, numTraces: 0, downloads: ds.downloads, lastModified: ds.lastModified };
          }
        })
      );
      const resolved = infos
        .filter((r): r is PromiseFulfilledResult<DatasetStats> => r.status === "fulfilled")
        .map((r) => r.value)
        .sort((a, b) => b.numTraces - a.numTraces);
      setDatasetStats(resolved);

      // Fetch sample rows from the biggest dataset for stats
      if (resolved.length > 0 && resolved[0].numTraces > 0) {
        try {
          const rowsRes = await fetch(
            `${VIEWER}/rows?dataset=${encodeURIComponent(resolved[0].repoId)}&config=default&split=train&offset=0&length=100`
          );
          if (rowsRes.ok) {
            const rowsData = await rowsRes.json();
            setSampleRows(rowsData.rows?.map((r: { row: TraceRow }) => r.row) || []);
          }
        } catch { /* ignore */ }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load: community
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

  // Compute stats
  const totalTraces = datasetStats.reduce((s, d) => s + d.numTraces, 0);
  const totalDownloads = datasetStats.reduce((s, d) => s + d.downloads, 0);
  const contributors = new Set(datasetStats.map(d => d.author)).size;
  const datasetCount = datasetStats.length;

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
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 32 }}>
        <div className="section-title" style={{ margin: 0 }}>Explorer</div>
        <div style={{ flex: 1 }} />
        <form onSubmit={handleUserSearch} style={{ display: "flex", gap: 0 }}>
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="HF username..."
            style={{
              fontFamily: "var(--font-mono)", fontSize: 12, padding: "8px 14px",
              border: "1px solid var(--border)", borderRight: "none",
              background: "var(--bg-alt)", color: "var(--text)",
              width: 200,
            }}
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
          : "All opentraces datasets on Hugging Face Hub."}
        {loading && " Loading..."}
        {error && <span style={{ color: "var(--red)" }}> {error}</span>}
      </p>

      {/* Top-line stats */}
      {!loading && (
        <div className="stats-row" style={{ marginBottom: 32 }}>
          <div className="stat-cell">
            <div className="stat-label">total traces</div>
            <div className="stat-value">{fmt(totalTraces)}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-label">datasets</div>
            <div className="stat-value">{datasetCount}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-label">contributors</div>
            <div className="stat-value">{contributors}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-label">downloads</div>
            <div className="stat-value">{fmt(totalDownloads)}</div>
          </div>
        </div>
      )}

      {/* Computed insights from sample data */}
      {sampleRows.length > 0 && (
        <>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 16 }}>
            insights (sampled from {sampleRows.length} traces)
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, marginBottom: 32 }}>
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

      {/* Dataset list */}
      {!loading && datasetStats.length > 0 && (
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
                  <td>{d.numTraces > 0 ? fmt(d.numTraces) : "-"}</td>
                  <td>{fmt(d.downloads)}</td>
                  <td style={{ color: "var(--text-dim)" }}>{new Date(d.lastModified).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Empty state */}
      {!loading && datasetStats.length === 0 && !error && (
        <div style={{ textAlign: "center", padding: "48px 0", color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
          {mode === "user"
            ? `No opentraces datasets found for "${username}".`
            : "No opentraces datasets found yet. Be the first!"}
          <br />
          <code style={{ color: "var(--accent)", marginTop: 8, display: "inline-block" }}>pip install opentraces && opentraces init</code>
        </div>
      )}
    </section>
  );
}
