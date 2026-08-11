// Projections index — ONE table of everything derived from an application
// and its agentic work. Two families:
//   · Datasets — STATIC projections: rows materialized by a workflow over
//     traces + labels. Nothing to play; you read them.
//   · Arenas — STATEFUL projections: a bench plays a scenario, a capsule
//     plays a trace, a gym plays a task family.
// This page is an INDEX, not a home. A bench's canonical home is its
// project (bench ↔ atlas ↔ evidence ↔ pulls is project-local); bench rows
// carry a project chip and deep-link back.

const ARX_KIND = {
  dataset: { icon: "datasets", label: "dataset", plays: "materialized from traces + labels", tone: "var(--c-write)" },
  bench:   { icon: "play",     label: "bench",   plays: "plays a scenario",                  tone: "var(--c-exec)" },
  capsule: { icon: "capsule",  label: "capsule", plays: "plays a trace",                     tone: "var(--c-read)" },
  gym:     { icon: "arena",    label: "gym",     plays: "plays a task family",               tone: "var(--c-plan)" },
};

const ARX_FILTERS = [
  { id: "all",     label: "All" },
  { id: "dataset", label: "Datasets" },
  { id: "bench",   label: "Benches" },
  { id: "capsule", label: "Capsules" },
  { id: "gym",     label: "Gyms" },
];

function arxBuildRows() {
  const home = window.ACCOUNT_HOME_REPO || "jayfarei/opentraces";
  const runs = (window.V2_RUNS || []).length;
  const rows = [];

  // Datasets — the static family.
  Object.entries(window.DATASET_DEFS || {}).forEach(([id, d]) => {
    rows.push({
      kind: "dataset", id, name: d.name, mono: true, desc: d.description,
      scope: null,
      meta: [
        { v: (d.rows || 0).toLocaleString(), k: "rows" },
        { v: String(d.inbox_count || 0), k: "inbox", hot: (d.inbox_count || 0) > 0 },
        { v: d.last_update, k: "updated" },
      ],
    });
  });

  // Benches — one per project. Only projects with runs are "warm".
  const benches = Object.entries(window.REPO_DEFS || {}).map(([id, r]) => {
    const warm = id === home;
    return {
      kind: "bench", id: "bench:" + id, name: (r.nm || id) + " bench",
      desc: warm
        ? "Scenario + verifier on a disposable box — the gate on every pull."
        : "No scenario drafted yet.",
      scope: { repo: id, label: (r.ns || "") + "/" + (r.nm || id) },
      meta: warm
        ? [{ v: String(runs), k: "runs" }, { v: "2h ago", k: "last run" }]
        : [{ v: "0", k: "runs" }, { v: "—", k: "last run" }],
      idle: !warm,
    };
  });
  rows.push(...benches.filter(b => !b.idle));

  // Capsules — sealed runs that travel; each replays one trace.
  (window.CAPSULES || []).forEach(c => {
    rows.push({
      kind: "capsule", id: c.id, name: c.title, desc: c.intent,
      scope: null,
      meta: [
        { v: c.cid.slice(4, 11), k: "cid", mono: true },
        { v: c.lifecycle, k: "lifecycle" },
        { v: c.publishedAt, k: "sealed" },
      ],
    });
  });

  // Gyms — graded attempts against a harvested task family.
  (window.V2_GYMS || []).forEach(g => {
    rows.push({
      kind: "gym", id: g.id, name: g.name, desc: g.job,
      scope: null, static: true,
      title: g.source,
      meta: [
        { v: String(g.tasks), k: "tasks" },
        { v: Math.round(g.passRate * 100) + "%", k: "pass" },
        { v: String(g.attempts), k: "attempts" },
        { v: g.lastRun, k: "last run" },
      ],
    });
  });

  rows.push(...benches.filter(b => b.idle));
  return rows;
}

function ProjectionsIndexPage({ onSelectDataset, onOpenBench, onOpenCapsule }) {
  const [q, setQ] = React.useState("");
  const [kind, setKind] = React.useState("all");
  const all = React.useMemo(arxBuildRows, []);
  const list = all.filter(r =>
    (kind === "all" || r.kind === kind) &&
    (!q || (r.name + " " + (r.desc || "")).toLowerCase().includes(q.toLowerCase())));

  const open = (r) => {
    if (r.kind === "dataset") onSelectDataset(r.id);
    else if (r.kind === "bench") onOpenBench(r.scope.repo);
    else if (r.kind === "capsule") onOpenCapsule(r.id);
  };

  return (
    <div className="landing landing-entity-index">
      <PageHero
        kicker="Workspace"
        title="Projections"
        subtitle="Everything derived from an application and its agentic work. Datasets are static — rows materialized by a workflow. Arenas are stateful — a bench plays a scenario, a capsule a trace, a gym a task family. Benches live inside their project; rows here link back to that home."
        actions={<ToolBtn icon="plus" label="New projection" primary data-agent-action="new-projection" data-agent-desc="Create a new projection — dataset, bench, capsule, or gym" data-agent-mutates="true" onClick={() => {}} />}
      />
      <div className="ov-dense">
        <div className="eix-toolbar">
          <label className="eix-search">
            <Icon name="search" size={13} className="sicon" />
            <input type="text" placeholder="Filter projections…" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
          <div className="arx-kinds" role="tablist" aria-label="Projection kind">
            {ARX_FILTERS.map(f => (
              <button key={f.id} className="arx-kchip" aria-pressed={kind === f.id} onClick={() => setKind(f.id)}>
                {f.label}
                <span className="mono n">{f.id === "all" ? all.length : all.filter(r => r.kind === f.id).length}</span>
              </button>
            ))}
          </div>
          <span className="eix-count mono">{q || kind !== "all" ? `${list.length} of ${all.length}` : all.length} projections</span>
        </div>
        <div className="eix-list">
          {list.map(r => {
            const K = ARX_KIND[r.kind];
            const Row = r.static ? "div" : "button";
            return (
              <Row key={r.id} className="eix-row arx-row" data-idle={r.idle ? "true" : "false"} data-static={r.static ? "true" : "false"} title={r.title} onClick={r.static ? undefined : () => open(r)}>
                <Icon name={K.icon} size={15} className="eix-icon" style={{ color: K.tone }} />
                <span className="eix-main">
                  <span className="eix-name">
                    <span className={r.mono ? "mono" : ""}>{r.name}</span>
                    <span className="arx-kind" style={{ color: K.tone }}>{K.label}</span>
                    {r.scope && <span className="arx-scope" title={"Lives in " + r.scope.label + " — opens the project bench"}><Icon name="git-branch" size={10} /> {r.scope.label}</span>}
                  </span>
                  <span className="eix-desc"><span className="arx-plays">{K.plays}</span> · {r.desc}</span>
                </span>
                <span className="eix-meta">
                  {r.meta.map(m => (
                    <span key={m.k} className={"eix-m" + (m.hot ? " hot" : "")}>
                      <span className={"v" + (m.mono ? " mono" : "")}>{m.v}</span>
                      <span className="k">{m.k}</span>
                    </span>
                  ))}
                </span>
              </Row>
            );
          })}
          {list.length === 0 && (
            <div className="eix-empty">No projections match “{q}”.</div>
          )}
        </div>
      </div>
    </div>
  );
}

window.ProjectionsIndexPage = ProjectionsIndexPage;
