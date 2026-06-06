// Trace compare — side-by-side A/B delta view (opentraces.trace_compare.v1).
// Pick trace A and B; render a two-column table grouped by section with a
// delta chip per row. Green when B improved, red when worse, grey when null.

function _cmpFmtVal(fmt, v) {
  if (v == null) return "—";
  switch (fmt) {
    case "tok":   return window.fmtTok(v);
    case "usd":   return "$" + Number(v).toFixed(2);
    case "dur":   return window.formatShort(v);
    case "pct":   return Math.round(v * 100) + "%";
    case "score": return Number(v).toFixed(2);
    default:      return String(v);
  }
}
function _cmpFmtDelta(fmt, d) {
  const sign = d > 0 ? "+" : d < 0 ? "−" : "±";
  const a = Math.abs(d);
  if (a === 0) return "±0";
  let body;
  switch (fmt) {
    case "tok":   body = window.fmtTok(a); break;
    case "usd":   body = "$" + a.toFixed(2); break;
    case "dur":   body = window.formatShort(a); break;
    case "pct":   body = Math.round(a * 100) + "pp"; break;
    case "score": body = a.toFixed(2); break;
    default:      body = String(a);
  }
  return sign + body;
}

function _cmpLeaf(cmp, group, row) {
  const d = cmp.delta;
  if (group === "quality") {
    if (row.persona) return d.quality.personas[row.key];
    if (row.overall) return d.quality.overall_utility;
  }
  return d[group][row.key];
}

function DeltaChip({ row, leaf }) {
  if (!leaf || leaf.delta == null) {
    return <span className="delta-chip" data-dir="na">n/a</span>;
  }
  const d = leaf.delta;
  let dir = "flat";
  if (d !== 0 && row.lowerBetter != null) {
    const improved = row.lowerBetter ? d < 0 : d > 0;
    dir = improved ? "up" : "down";
  }
  const arrow = d > 0 ? "▲" : d < 0 ? "▼" : "";
  return (
    <span className="delta-chip" data-dir={dir}>
      {arrow && <span className="dc-arrow">{arrow}</span>}
      {_cmpFmtDelta(row.fmt, d)}
    </span>
  );
}

function TracePicker({ side, traces, value, onChange, fidelity }) {
  const tr = traces.find(t => t.id === value) || traces[0];
  return (
    <div className="cmp-pick" data-side={side}>
      <div className="cp-side">
        <span>{side === "a" ? "Trace A · baseline" : "Trace B · candidate"}</span>
        {fidelity && <FidelityBadge fidelity={fidelity} />}
      </div>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {traces.map(t => (
          <option key={t.id} value={t.id}>{t.title} · {relTime(t.timestamp)}</option>
        ))}
      </select>
      <div className="cp-meta">
        <AgentGlyph agent={tr.agent} size={13} />
        <span>{tr.agent.name}</span>
        <span className="cp-id mono">{tr.id}</span>
        <RepoLabel repo={tr.repo} />
      </div>
    </div>
  );
}

function CompareView({ traces, initialA, initialB }) {
  const opts = traces;
  const [aId, setAId] = React.useState(initialA || (opts[3] && opts[3].id) || opts[0].id);
  const [bId, setBId] = React.useState(initialB || opts[0].id);
  const a = opts.find(t => t.id === aId) || opts[0];
  const b = opts.find(t => t.id === bId) || opts[1];
  const cmp = React.useMemo(() => buildCompare(a, b), [a.id, b.id]);

  const swap = () => { setAId(bId); setBId(aId); };

  // Tally how B did overall (improvements vs regressions across directional rows).
  const tally = React.useMemo(() => {
    let better = 0, worse = 0;
    COMPARE_SECTIONS.forEach(sec => sec.rows.forEach(row => {
      if (row.lowerBetter == null) return;
      const leaf = _cmpLeaf(cmp, sec.group, row);
      if (!leaf || leaf.delta == null || leaf.delta === 0) return;
      const improved = row.lowerBetter ? leaf.delta < 0 : leaf.delta > 0;
      if (improved) { better += 1; } else { worse += 1; }
    }));
    return { better, worse };
  }, [cmp]);

  return (
    <div className="landing compare-view">
      <div className="cmp-head">
        <h1 className="ll-h1">
          Compare runs
          <FidelityBadge fidelity={cmp.fidelity} />
        </h1>
        <p className="cmp-sub">
          Frozen <span className="mono">{cmp.schema}</span> delta — every leaf is an {"{a, b, delta}"} triple.
          Green where B improved (fewer tokens, lower cost, higher quality, fewer errors), red where it regressed,
          grey where a side is unavailable.
        </p>
      </div>

      <div className="cmp-pickers">
        <TracePicker side="a" traces={opts} value={aId} onChange={setAId} fidelity={cmp.fidelity_a} />
        <div className="cmp-swap">
          <button onClick={swap} title="Swap A and B" aria-label="Swap A and B">
            <Icon name="back" size={15} />
          </button>
        </div>
        <TracePicker side="b" traces={opts} value={bId} onChange={setBId} fidelity={cmp.fidelity_b} />
      </div>

      <div className="cmp-table">
        <div className="cmp-trow cmp-thead">
          <div className="ct-cell ct-label">
            B vs A · <span style={{ color: "var(--c-recover)" }}>{tally.better} better</span> ·{" "}
            <span style={{ color: "var(--c-fail)" }}>{tally.worse} worse</span>
          </div>
          <div className="ct-cell ct-a">A</div>
          <div className="ct-cell ct-b">B</div>
          <div className="ct-cell ct-d">Δ delta</div>
        </div>
        {COMPARE_SECTIONS.map(sec => (
          <React.Fragment key={sec.group}>
            <div className="cmp-trow cmp-sec-head">
              <div className="cs-title">{sec.title}</div>
            </div>
            {sec.rows.map(row => {
              const leaf = _cmpLeaf(cmp, sec.group, row);
              const naSide = leaf && (leaf.a == null || leaf.b == null);
              return (
                <div key={row.key} className={"cmp-trow" + (row.strong ? " strong" : "")}>
                  <div className={"ct-label" + (row.strong ? " strong" : "")}>
                    {row.label}
                    {naSide && <span className="lb-na">partial</span>}
                  </div>
                  <div className={"ct-a" + (leaf && leaf.a == null ? " ct-na" : "")}>
                    {leaf && leaf.a == null ? "n/a" : _cmpFmtVal(row.fmt, leaf ? leaf.a : null)}
                  </div>
                  <div className={"ct-b" + (leaf && leaf.b == null ? " ct-na" : "")}>
                    {leaf && leaf.b == null ? "n/a" : _cmpFmtVal(row.fmt, leaf ? leaf.b : null)}
                  </div>
                  <div className="ct-d"><DeltaChip row={row} leaf={leaf} /></div>
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

window.CompareView = CompareView;
