"use client";

import { useEffect, useState, useCallback, useRef, FormEvent } from "react";

const HF_API = "https://huggingface.co/api";
const VIEWER = "https://datasets-server.huggingface.co";
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

interface CacheEntry {
  datasetStats: DatasetStats[];
  sampleRows: TraceRow[];
  readmeStatsList: ReadmeStats[];
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

function writeCache(key: string, datasetStats: DatasetStats[], sampleRows: TraceRow[], readmeStatsList: ReadmeStats[]) {
  try {
    sessionStorage.setItem(key, JSON.stringify({ datasetStats, sampleRows, readmeStatsList, ts: Date.now() }));
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

interface ReadmeStats {
  total_traces: number;
  total_tokens: number;
  avg_steps_per_session: number | null;
  success_rate: number | null;
  top_dependencies: [string, number][];
  agent_counts: Record<string, number>;
  model_counts: Record<string, number>;
}

function aggregateReadmeStats(statsList: ReadmeStats[]): ReadmeStats | null {
  if (!statsList.length) return null;
  const total = statsList.reduce((s, r) => s + r.total_traces, 0);
  if (!total) return null;

  let stepSum = 0, successSum = 0, successN = 0, totalTokens = 0;
  const agents: Record<string, number> = {};
  const models: Record<string, number> = {};
  const depMap: Record<string, number> = {};

  for (const s of statsList) {
    const n = s.total_traces;
    totalTokens += s.total_tokens || 0;
    if (s.avg_steps_per_session != null) stepSum += s.avg_steps_per_session * n;
    if (s.success_rate != null) { successSum += s.success_rate * n; successN += n; }
    for (const [k, v] of Object.entries(s.agent_counts || {})) agents[k] = (agents[k] || 0) + v;
    for (const [k, v] of Object.entries(s.model_counts || {})) models[k] = (models[k] || 0) + v;
    for (const [k, v] of (s.top_dependencies || [])) {
      const trimmed = k.trim();
      if (trimmed.length > 1 && !/^[&|;<>!]+$/.test(trimmed)) depMap[trimmed] = (depMap[trimmed] || 0) + v;
    }
  }

  const top_dependencies: [string, number][] = Object.entries(depMap)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10) as [string, number][];

  return {
    total_traces: total,
    total_tokens: totalTokens,
    avg_steps_per_session: total > 0 ? Math.round(stepSum / total) : null,
    success_rate: successN > 0 ? Math.round(successSum / successN) : null,
    top_dependencies,
    agent_counts: agents,
    model_counts: models,
  };
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
  const [readmeStatsList, setReadmeStatsList] = useState<ReadmeStats[]>([]);
  const [phase, setPhase] = useState<"loading" | "datasets" | "enriching" | "done">("loading");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetchDatasets = useCallback(async (query: string, isUser: boolean) => {
    const key = cacheKey(query, isUser);
    const cached = readCache(key);

    if (cached) {
      setDatasetStats(cached.datasetStats);
      setReadmeStatsList(cached.readmeStatsList || []);
      setPhase("done");
      setRefreshing(true);
    } else {
      setPhase("loading");
      setError(null);
      setDatasetStats([]);
      setReadmeStatsList([]);
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

      // Fire /info + README requests in parallel, stream results as they arrive
      const enriched = [...initial];

      // Fetch README for trace count + pre-computed stats block (written by dataset_card.py on each push)
      // Much lighter than fetching full JSONL shards — README is a few KB
      async function fetchReadmeData(repoId: string, signal: AbortSignal): Promise<{ count: number; stats: ReadmeStats | null }> {
        try {
          const url = `https://huggingface.co/datasets/${repoId}/resolve/main/README.md`;
          const r = await fetch(url, { signal });
          if (!r.ok) return { count: 0, stats: null };
          const text = await r.text();

          const countMatch = text.match(/\|\s*Total traces\s*\|\s*([\d,]+)\s*\|/i);
          const count = countMatch ? parseInt(countMatch[1].replace(/,/g, ""), 10) : 0;

          const tokensMatch = text.match(/\|\s*Total tokens\s*\|\s*([\d,]+)\s*\|/i);
          const tableTokens = tokensMatch ? parseInt(tokensMatch[1].replace(/,/g, ""), 10) : 0;

          let stats: ReadmeStats | null = null;
          const statsMatch = text.match(/<!--\s*opentraces:stats\s*(\{[\s\S]*?\})\s*-->/);
          if (statsMatch) {
            try {
              const raw = JSON.parse(statsMatch[1]);
              stats = {
                total_traces: raw.total_traces || count,
                total_tokens: raw.total_tokens || tableTokens,
                avg_steps_per_session: raw.avg_steps_per_session ?? null,
                success_rate: raw.success_rate ?? null,
                top_dependencies: raw.top_dependencies || [],
                agent_counts: raw.agent_counts || {},
                model_counts: raw.model_counts || {},
              };
            } catch { /* malformed JSON, ignore */ }
          } else if (count > 0) {
            // Older README without stats block — use what we can parse from the table
            stats = {
              total_traces: count,
              total_tokens: tableTokens,
              avg_steps_per_session: null,
              success_rate: null,
              top_dependencies: [],
              agent_counts: {},
              model_counts: {},
            };
          }

          return { count, stats };
        } catch { return { count: 0, stats: null }; }
      }

      // Stream /info results as they complete
      // Track separately: datasets-server count (used for row fetching) vs display count
      // Collect readme stats in a local array so they can be saved to cache after all settle
      const collectedReadmeStats: ReadmeStats[] = [];

      const infoPromises = filtered.map(async (ds, idx) => {
        let serverCount = 0;
        try {
          const r = await fetch(`${VIEWER}/info?dataset=${encodeURIComponent(ds.id)}`, { signal: sig });
          if (r.ok) {
            const info = await r.json();
            // Config name varies per dataset — sum across all configs and splits
            const datasetInfo = info?.dataset_info ?? {};
            serverCount = Object.values(datasetInfo).reduce((total: number, config) => {
              const splits = (config as { splits?: Record<string, { num_examples?: number }> })?.splits ?? {};
              return total + Object.values(splits).reduce((s, split) => s + (split?.num_examples ?? 0), 0);
            }, 0);
          }
        } catch { /* ignore, serverCount stays 0 */ }

        enriched[idx] = { ...enriched[idx], numTraces: serverCount || null };
        if (!sig.aborted) {
          setDatasetStats([...enriched].sort((a, b) => (b.numTraces ?? 0) - (a.numTraces ?? 0)));
        }

        // Always fetch README — collects pre-computed stats block for community insights.
        // Also updates count display if datasets-server didn't index this dataset (JSONL-only).
        try {
          const { count: rawCount, stats } = await fetchReadmeData(ds.id, sig);
          if (sig.aborted) return;
          if (serverCount === 0) {
            enriched[idx] = { ...enriched[idx], numTraces: rawCount };
            setDatasetStats([...enriched].sort((a, b) => (b.numTraces ?? 0) - (a.numTraces ?? 0)));
          }
          if (stats) {
            collectedReadmeStats.push(stats);
            setReadmeStatsList([...collectedReadmeStats]);
          }
        } catch {
          if (serverCount === 0) {
            enriched[idx] = { ...enriched[idx], numTraces: 0 };
            if (!sig.aborted) setDatasetStats([...enriched].sort((a, b) => (b.numTraces ?? 0) - (a.numTraces ?? 0)));
          }
        }
      });

      await Promise.allSettled(infoPromises);
      if (sig.aborted) return;

      const finalStats = [...enriched].sort((a, b) => (b.numTraces ?? 0) - (a.numTraces ?? 0));
      setDatasetStats(finalStats);
      writeCache(key, finalStats, [], collectedReadmeStats);
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

  // Community stats aggregated from README stats blocks (pre-computed on each push)
  const communityStats = aggregateReadmeStats(readmeStatsList);
  const csAgents = communityStats ? Object.entries(communityStats.agent_counts).sort((a, b) => b[1] - a[1]) : [];
  const csModels = communityStats ? Object.entries(communityStats.model_counts).sort((a, b) => b[1] - a[1]) : [];
  const csDeps = communityStats?.top_dependencies ?? [];

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
            <div className="stat-label">total tokens</div>
            <div className="stat-value">{communityStats ? fmt(communityStats.total_tokens) : <Skeleton width={48} height={28} />}</div>
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

      {/* Community stats aggregated from README stats blocks */}
      {communityStats !== null && (
        <>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 16 }}>
            community stats (aggregated from {fmt(communityStats.total_traces)} traces)
          </div>

          <div className="insights-grid">
            {/* Agent & Model breakdown */}
            <div style={{ border: "1px solid var(--border)", padding: 20, background: "var(--surface)" }}>
              <div className="stat-label" style={{ marginBottom: 16 }}>agents & models</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {csAgents.length > 0 && (
                  <>
                    <div style={{ fontSize: 9, color: "var(--text-dim)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 6 }}>agents</div>
                    {csAgents.slice(0, 4).map(([name, count]) => (
                      <div key={name} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", color: "var(--text-muted)" }}>
                        <span>{name}</span>
                        <span style={{ color: "var(--text-dim)" }}>{Math.round((count / communityStats.total_traces) * 100)}%</span>
                      </div>
                    ))}
                  </>
                )}
                {csModels.length > 0 && (
                  <>
                    <div style={{ fontSize: 9, color: "var(--text-dim)", letterSpacing: "0.1em", textTransform: "uppercase", marginTop: 12, marginBottom: 6 }}>models</div>
                    {csModels.slice(0, 4).map(([name, count]) => (
                      <div key={name} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", color: "var(--text-muted)" }}>
                        <span style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
                        <span style={{ color: "var(--text-dim)" }}>{Math.round((count / communityStats.total_traces) * 100)}%</span>
                      </div>
                    ))}
                  </>
                )}
                {csAgents.length === 0 && csModels.length === 0 && (
                  <div style={{ color: "var(--text-dim)", fontSize: 11 }}>No agent/model data</div>
                )}
              </div>
            </div>

            {/* Dependencies */}
            <div style={{ border: "1px solid var(--border)", padding: 20, background: "var(--surface)" }}>
              <div className="stat-label" style={{ marginBottom: 16 }}>top dependencies</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {csDeps.length > 0 ? csDeps.map(([dep, count]) => (
                  <div key={dep} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", color: "var(--text-muted)" }}>
                    <span>{dep}</span>
                    <span style={{ color: "var(--text-dim)" }}>{fmt(count)}</span>
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
      {phase === "enriching" && communityStats === null && (
        <>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 16 }}>
            <Skeleton width={200} height={12} />
          </div>
          <div className="insights-grid">
            {[0, 1].map(i => (
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
