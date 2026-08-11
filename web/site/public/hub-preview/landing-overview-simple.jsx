// Overview widgets — the simplified stat strip and the re-sliceable token
// distribution. Loaded before landing-overview-v2.jsx; exported on window.
//
// The distribution re-slices ONE seeded yearly total four ways: by project
// (anchor), by model, by harness, and — on the organisation account — by
// member. Model/harness shares are weighted by real trace counts so the
// slices agree with the ledger.

const TDX_REPO_COLORS = {
  "jayfarei/opentraces":     "var(--c-write)",
  "jayfarei/datafetch":      "var(--c-read)",
  "jayfarei/lazymem":        "var(--c-plan)",
  "jayfarei/clarify":        "var(--c-exec)",
  "jayfarei/open-data":      "var(--c-git)",
  "jayfarei/koreader-kiosk": "var(--c-user)",
  ...(window.ORG_REPO_COLORS || {}),
};

const TDX_PALETTE = ["var(--c-read)", "var(--c-write)", "var(--c-plan)", "var(--c-exec)", "var(--c-git)", "var(--c-user)"];

const TDX_MEMBERS = [
  { name: "Otto", agent: true, w: 0.42 },
  { name: "Gabriele Farei", w: 0.21 },
  { name: "Mara Voss", w: 0.16 },
  { name: "Wei Zhang", w: 0.12 },
  { name: "Nadia Flores", w: 0.09 },
];

const TDX_DIM_NOUN = { project: "projects", model: "models", harness: "harnesses", member: "members" };

// Seeded per-project totals — the anchor every other dimension re-slices.
function tdxBase() {
  let seed = 414141;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  return Object.keys(window.REPO_DEFS || {}).map(id => {
    const d = REPO_DEFS[id];
    const base = d.total_traces * (180000 + rnd() * 120000);
    const tokens = Math.round(base * (0.6 + rnd() * 0.9));
    const inTok = Math.round(tokens * (0.7 + rnd() * 0.15));
    const outTok = tokens - inTok;
    const cost = (inTok / 1e6) * 3 + (outTok / 1e6) * 15;
    const delta = (rnd() - 0.45) * 0.6;
    return { id, d, tokens, inTok, outTok, cost, delta };
  });
}

function tdxRows(dim) {
  const base = tdxBase();
  const total = base.reduce((a, r) => a + r.tokens, 0);
  const totalCost = base.reduce((a, r) => a + r.cost, 0);
  let seed = 727272;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };

  if (dim === "project") {
    return base
      .map(r => ({ key: r.id, label: r.d.nm, prefix: r.d.ns + "/", tokens: r.tokens, cost: r.cost, delta: r.delta, color: TDX_REPO_COLORS[r.id] || "var(--c-git)" }))
      .sort((a, b) => b.tokens - a.tokens);
  }

  if (dim === "member") {
    return TDX_MEMBERS.map((m, i) => ({
      key: m.name, label: m.name, agent: m.agent,
      tokens: Math.round(total * m.w), cost: totalCost * m.w,
      delta: (rnd() - 0.45) * 0.6,
      color: TDX_PALETTE[i % TDX_PALETTE.length],
    }));
  }

  // model / harness — weighted by real trace counts.
  const traces = window.ALL_TRACES || window.RECENT_TRACES || [];
  const counts = new Map();
  traces.forEach(t => {
    const k = dim === "model" ? t.agent.model : t.agent.name;
    counts.set(k, (counts.get(k) || 0) + 1);
  });
  const n = traces.length || 1;
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([k, c], i) => ({
      key: k, label: k,
      tokens: Math.round(total * c / n), cost: totalCost * c / n,
      delta: (rnd() - 0.45) * 0.6,
      color: TDX_PALETTE[i % TDX_PALETTE.length],
    }));
}

const tdxFmtTok = (n) => {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(0) + "k";
  return String(n);
};
const tdxFmtCost = (c) => "$" + (c >= 1000 ? c.toFixed(0) : c.toFixed(2));
const tdxFmtDelta = (d) => (d > 0 ? "+" : "") + (d * 100).toFixed(0) + "%";

function TokenDistribution() {
  const isOrg = window.OT_ACCOUNT === "org";
  const dims = [
    { id: "project", label: "By project" },
    { id: "model",   label: "By model" },
    { id: "harness", label: "By harness" },
    ...(isOrg ? [{ id: "member", label: "By member" }] : []),
  ];
  const [dim, setDim] = React.useState("project");
  const rows = React.useMemo(() => tdxRows(dim), [dim]);
  const total = rows.reduce((a, r) => a + r.tokens, 0);
  const totalCost = rows.reduce((a, r) => a + r.cost, 0);

  return (
    <section className="tok-dist">
      <header className="tok-dist-head">
        <div>
          <div className="tok-dist-kicker">Tokens · Last 365d</div>
          <div className="tok-dist-title">Distribution</div>
        </div>
        <div className="tok-dist-totals ovs2-flat">
          <div className="ovs2-cell">
            <span className="ovs2-k">Tokens spent</span>
            <span className="ovs2-v mono">{tdxFmtTok(total)}</span>
          </div>
          <div className="ovs2-cell">
            <span className="ovs2-k">Total cost</span>
            <span className="ovs2-v mono">{tdxFmtCost(totalCost)}</span>
          </div>
          <div className="ovs2-cell">
            <span className="ovs2-k">{TDX_DIM_NOUN[dim]}</span>
            <span className="ovs2-v mono">{rows.length}</span>
          </div>
        </div>
        <div className="hm-toggle tok-dist-toggle" role="tablist">
          {dims.map(d => (
            <button key={d.id} className={"hmt " + (dim === d.id ? "on" : "")} onClick={() => setDim(d.id)}>{d.label}</button>
          ))}
        </div>
      </header>

      <div className="tok-dist-bar" role="img" aria-label={"Token distribution " + dims.find(d => d.id === dim).label.toLowerCase()}>
        {rows.map(r => {
          const pct = (r.tokens / total) * 100;
          return (
            <div
              key={r.key}
              className="tdb-seg"
              style={{ flexBasis: pct + "%", background: r.color }}
              title={(r.prefix || "") + r.label + " · " + tdxFmtTok(r.tokens) + " (" + pct.toFixed(1) + "%)"}
            >
              {pct > 6 && <span className="tdb-seg-label mono">{r.label}</span>}
            </div>
          );
        })}
      </div>

      <div className="tok-dist-chips">
        {rows.map(r => {
          const pct = (r.tokens / total) * 100;
          const up = r.delta >= 0;
          return (
            <div key={r.key} className="td-chip">
              <span className="td-chip-swatch" style={{ background: r.color }} />
              <div className="td-chip-main">
                <div className="td-chip-name">
                  {r.prefix && <span className="ns">{r.prefix}</span>}<span className="nm">{r.label}</span>
                  {r.agent && <Icon name="bot" size={11} className="td-chip-bot" />}
                </div>
                <div className="td-chip-meta">
                  <span className="mono">{tdxFmtTok(r.tokens)}</span>
                  <span className="td-chip-sep">·</span>
                  <span className="mono">{pct.toFixed(1)}%</span>
                  <span className="td-chip-sep">·</span>
                  <span className="mono">{tdxFmtCost(r.cost)}</span>
                </div>
              </div>
              <div className={"td-chip-delta " + (up ? "up" : "down")} title="vs prior 365d window">
                <span className="arrow">{up ? "▲" : "▼"}</span>
                <span className="mono">{tdxFmtDelta(Math.abs(r.delta))}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// The simplified stat strip: traces, tokens, projects, datasets — then the
// projections that play: benches, capsules, gyms. Counts only; the items
// themselves live in their own indexes.
function OvStatsSimple() {
  const s = React.useMemo(() => {
    const base = tdxBase();
    let inTok = 0, outTok = 0;
    base.forEach(r => { inTok += r.inTok; outTok += r.outTok; });
    let traces = 0;
    (window.HEATMAP || []).forEach(w => w.forEach(c => { traces += c.count; }));
    return {
      traces, inTok, outTok,
      projects: Object.keys(window.REPO_DEFS || {}).length,
      datasets: Object.keys(window.DATASET_DEFS || {}).length,
      benches: Object.keys(window.REPO_DEFS || {}).length,
      capsules: (window.CAPSULES || []).length,
      gyms: (window.V2_GYMS || []).length,
    };
  }, []);

  const fmt = (n) => {
    if (n >= 1000000000) return (n / 1000000000).toFixed(1).replace(/\.0$/, "") + "B";
    if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  };

  const cells = [
    { k: "Traces",     v: fmt(s.traces) },
    { k: "Tokens in",  v: fmt(s.inTok),  arr: "in" },
    { k: "Tokens out", v: fmt(s.outTok), arr: "out" },
    { div: true },
    { k: "Projects", v: String(s.projects), icon: "git-branch" },
    { k: "Datasets", v: String(s.datasets), icon: "datasets" },
    { k: "Benches",  v: String(s.benches),  icon: "play" },
    { k: "Capsules", v: String(s.capsules), icon: "capsule" },
    { k: "Gyms",     v: String(s.gyms),     icon: "arena" },
  ];

  return (
    <div className="ovs2">
      {cells.map((c, i) => c.div
        ? <span key={"div" + i} className="ovs2-div" />
        : (
          <div key={c.k} className="ovs2-cell">
            <span className="ovs2-k">
              {c.icon && <Icon name={c.icon} size={12} className="ovs2-kicon" />}
              {c.arr === "in" && <Icon name="down-line" size={11} className="ovs2-arr arr-in" />}
              {c.arr === "out" && <Icon name="up-line" size={11} className="ovs2-arr arr-out" />}
              {c.k}
            </span>
            <span className="ovs2-v mono">{c.v}</span>
          </div>
        ))}
      <span className="ovs2-note mono">last 12 months</span>
    </div>
  );
}

Object.assign(window, { TokenDistribution, OvStatsSimple });
