"use client";

import { useEffect, useState, useCallback } from "react";

const HF_API = "https://huggingface.co/api";
const VIEWER = "https://datasets-server.huggingface.co";

interface HFDataset {
  id: string;
  author: string;
  lastModified: string;
  downloads: number;
  tags: string[];
}

interface TraceRow {
  agent?: string | { name?: string; model?: string };
  model?: string;
  metrics?: { total_steps?: number; estimated_cost_usd?: number };
  outcome?: { success?: boolean } | boolean;
  security?: { tier?: string } | string;
  dependencies?: string[];
  timestamp_start?: string;
}

interface DatasetInfo {
  repoId: string;
  numTraces: number;
}

function getAgent(r: TraceRow): string {
  if (typeof r.agent === "string") return r.agent;
  return r.agent?.name || "-";
}

function getModel(r: TraceRow): string {
  if (r.model) return r.model;
  if (typeof r.agent === "object") return r.agent?.model || "-";
  return "-";
}

function getSuccess(r: TraceRow): boolean | null {
  if (typeof r.outcome === "boolean") return r.outcome;
  if (typeof r.outcome === "object") return r.outcome?.success ?? null;
  return null;
}

function getTier(r: TraceRow): string {
  if (typeof r.security === "string") return r.security;
  if (typeof r.security === "object") return r.security?.tier || "-";
  return "-";
}

export default function Dashboard() {
  const [datasets, setDatasets] = useState<HFDataset[]>([]);
  const [datasetInfos, setDatasetInfos] = useState<DatasetInfo[]>([]);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [rows, setRows] = useState<TraceRow[]>([]);
  const [totalRows, setTotalRows] = useState(0);
  const [loading, setLoading] = useState(true);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState("");
  const [modelFilter, setModelFilter] = useState("");

  // Fetch datasets
  useEffect(() => {
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${HF_API}/datasets?search=opentraces&sort=downloads&direction=-1&limit=50`);
        if (!res.ok) throw new Error(`HF API ${res.status}`);
        const data: HFDataset[] = await res.json();
        setDatasets(data);

        // Fan out /info requests for row counts
        const infos = await Promise.allSettled(
          data.map(async (ds) => {
            const r = await fetch(`${VIEWER}/info?dataset=${encodeURIComponent(ds.id)}`);
            if (!r.ok) return { repoId: ds.id, numTraces: 0 };
            const info = await r.json();
            const num = info?.dataset_info?.default?.splits?.train?.num_examples ?? 0;
            return { repoId: ds.id, numTraces: num };
          })
        );
        const resolved = infos
          .filter((r): r is PromiseFulfilledResult<DatasetInfo> => r.status === "fulfilled")
          .map((r) => r.value);
        setDatasetInfos(resolved);

        if (data.length > 0) setSelectedDataset(data[0].id);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to fetch");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  // Fetch rows for selected dataset
  const fetchRows = useCallback(async (datasetId: string) => {
    if (!datasetId) return;
    setRowsLoading(true);
    try {
      const res = await fetch(
        `${VIEWER}/rows?dataset=${encodeURIComponent(datasetId)}&config=default&split=train&offset=0&length=100`
      );
      if (!res.ok) {
        // Fallback: try first-rows
        const res2 = await fetch(
          `${VIEWER}/first-rows?dataset=${encodeURIComponent(datasetId)}&config=default&split=train`
        );
        if (!res2.ok) throw new Error("No data");
        const data = await res2.json();
        setRows(data.rows?.map((r: { row: TraceRow }) => r.row) || []);
        setTotalRows(data.rows?.length || 0);
        return;
      }
      const data = await res.json();
      setRows(data.rows?.map((r: { row: TraceRow }) => r.row) || []);
      setTotalRows(data.num_rows_total || data.rows?.length || 0);
    } catch {
      setRows([]);
      setTotalRows(0);
    } finally {
      setRowsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedDataset) fetchRows(selectedDataset);
  }, [selectedDataset, fetchRows]);

  // Filter rows client-side
  const filteredRows = rows.filter((r) => {
    if (agentFilter && getAgent(r) !== agentFilter) return false;
    if (modelFilter && !getModel(r).includes(modelFilter)) return false;
    return true;
  });

  // Stats
  const traceCount = datasetInfos.find((d) => d.repoId === selectedDataset)?.numTraces || totalRows;
  const successCount = filteredRows.filter((r) => getSuccess(r) === true).length;
  const successRate = filteredRows.length > 0 ? Math.round((successCount / filteredRows.length) * 100) : 0;
  const avgCost = filteredRows.length > 0
    ? (filteredRows.reduce((s, r) => s + (r.metrics?.estimated_cost_usd || 0), 0) / filteredRows.length).toFixed(2)
    : "0.00";
  const avgSteps = filteredRows.length > 0
    ? Math.round(filteredRows.reduce((s, r) => s + (r.metrics?.total_steps || 0), 0) / filteredRows.length)
    : 0;

  // Community totals
  const communityTraces = datasetInfos.reduce((s, d) => s + d.numTraces, 0);
  const contributors = new Set(datasets.map((d) => d.author)).size;

  // Unique filter values
  const agents = [...new Set(rows.map(getAgent).filter((a) => a !== "-"))];
  const models = [...new Set(rows.map(getModel).filter((m) => m !== "-"))];

  return (
    <section style={{ padding: "48px 0" }}>
      <div className="section-title">Dashboard</div>
      <p className="section-sub">
        Live data from Hugging Face Hub. {communityTraces > 0 && `${communityTraces.toLocaleString()} traces across ${contributors} contributors.`}
      </p>

      {/* Dataset selector */}
      <div style={{ marginBottom: 24 }}>
        <div className="stat-label" style={{ marginBottom: 8 }}>dataset</div>
        {loading ? (
          <div style={{ color: "var(--text-muted)", fontSize: 12, fontFamily: "var(--font-mono)" }}>loading...</div>
        ) : error ? (
          <div style={{ color: "var(--red)", fontSize: 12, fontFamily: "var(--font-mono)" }}>{error}</div>
        ) : datasets.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 12, fontFamily: "var(--font-mono)" }}>
            No opentraces datasets found yet. Be the first: <code>pip install opentraces</code>
          </div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {datasets.map((d) => {
              const info = datasetInfos.find((i) => i.repoId === d.id);
              return (
                <button
                  key={d.id}
                  onClick={() => setSelectedDataset(d.id)}
                  style={{
                    fontFamily: "var(--font-mono)", fontSize: 11, padding: "6px 12px",
                    border: `1px solid ${selectedDataset === d.id ? "var(--accent)" : "var(--border)"}`,
                    background: selectedDataset === d.id ? "var(--accent-bg)" : "var(--surface)",
                    color: selectedDataset === d.id ? "var(--accent)" : "var(--text-secondary)",
                    cursor: "pointer",
                  }}
                >
                  {d.id}
                  {info && info.numTraces > 0 && (
                    <span style={{ color: "var(--text-dim)", marginLeft: 8 }}>{info.numTraces} traces</span>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Filters */}
      {rows.length > 0 && (agents.length > 0 || models.length > 0) && (
        <div style={{ display: "flex", gap: 12, marginBottom: 24, flexWrap: "wrap" }}>
          {agents.length > 0 && (
            <div>
              <div className="stat-label" style={{ marginBottom: 4 }}>agent</div>
              <select value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}
                style={{ fontFamily: "var(--font-mono)", fontSize: 11, padding: "6px 10px", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)" }}>
                <option value="">all</option>
                {agents.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
          )}
          {models.length > 0 && (
            <div>
              <div className="stat-label" style={{ marginBottom: 4 }}>model</div>
              <select value={modelFilter} onChange={(e) => setModelFilter(e.target.value)}
                style={{ fontFamily: "var(--font-mono)", fontSize: 11, padding: "6px 10px", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)" }}>
                <option value="">all</option>
                {models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          )}
        </div>
      )}

      {/* Stats row */}
      {selectedDataset && (
        <div className="stats-row" style={{ marginBottom: 24 }}>
          <div className="stat-cell">
            <div className="stat-label">traces</div>
            <div className="stat-value">{rowsLoading ? "..." : traceCount.toLocaleString()}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-label">success rate</div>
            <div className="stat-value">{rowsLoading ? "..." : `${successRate}%`}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-label">avg cost</div>
            <div className="stat-value">{rowsLoading ? "..." : `$${avgCost}`}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-label">avg steps</div>
            <div className="stat-value">{rowsLoading ? "..." : avgSteps}</div>
          </div>
        </div>
      )}

      {/* Traces table */}
      {selectedDataset && (
        <div className="tbl-wrap">
          <div className="tbl-head">
            <span className="tbl-title">traces</span>
            <span style={{ fontSize: 10, color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>
              showing {Math.min(filteredRows.length, 25)} of {totalRows.toLocaleString()}
            </span>
          </div>
          {rowsLoading ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)", fontSize: 12, fontFamily: "var(--font-mono)" }}>
              loading from datasets-server.huggingface.co ...
            </div>
          ) : filteredRows.length === 0 ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)", fontSize: 12, fontFamily: "var(--font-mono)" }}>
              no matching rows
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>agent</th>
                  <th>model</th>
                  <th>steps</th>
                  <th>cost</th>
                  <th>outcome</th>
                  <th>tier</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.slice(0, 25).map((r, i) => {
                  const success = getSuccess(r);
                  return (
                    <tr key={i}>
                      <td>{getAgent(r)}</td>
                      <td style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{getModel(r)}</td>
                      <td>{r.metrics?.total_steps || "-"}</td>
                      <td>{r.metrics?.estimated_cost_usd ? `$${r.metrics.estimated_cost_usd.toFixed(2)}` : "-"}</td>
                      <td>
                        <span className={`badge ${success === true ? "badge-ok" : success === false ? "badge-er" : ""}`}>
                          {success === true ? "success" : success === false ? "failed" : "-"}
                        </span>
                      </td>
                      <td>{getTier(r)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Breakdowns */}
      {filteredRows.length > 0 && (
        <div style={{ marginTop: 24, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <div style={{ border: "1px solid var(--border)", padding: 16, background: "var(--surface)" }}>
            <div className="stat-label" style={{ marginBottom: 12 }}>top dependencies</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
              {(() => {
                const counts: Record<string, number> = {};
                filteredRows.forEach((r) => r.dependencies?.forEach((d) => { counts[d] = (counts[d] || 0) + 1; }));
                const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
                return entries.length > 0 ? entries.map(([dep, count]) => (
                  <div key={dep} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", color: "var(--text-muted)" }}>
                    <span>{dep}</span><span style={{ color: "var(--text-dim)" }}>{count}</span>
                  </div>
                )) : <div style={{ color: "var(--text-dim)" }}>no dependency data</div>;
              })()}
            </div>
          </div>
          <div style={{ border: "1px solid var(--border)", padding: 16, background: "var(--surface)" }}>
            <div className="stat-label" style={{ marginBottom: 12 }}>model distribution</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
              {(() => {
                const counts: Record<string, number> = {};
                filteredRows.forEach((r) => { counts[getModel(r)] = (counts[getModel(r)] || 0) + 1; });
                return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([model, count]) => (
                  <div key={model} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", color: "var(--text-muted)" }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 180 }}>{model}</span>
                    <span style={{ color: "var(--text-dim)" }}>{Math.round((count / filteredRows.length) * 100)}%</span>
                  </div>
                ));
              })()}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
