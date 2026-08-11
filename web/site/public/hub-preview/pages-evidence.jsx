// pages-evidence.jsx — Evidence index. One frozen run record per row,
// rendered twice (page for people, feed for agents). This list is the
// people-side entry point.

function EvidenceRow({ ev, onOpen, onOpenTrace }) {
  const run = v2Run(ev.run) || {};
  const scn = v2Scenario(run.scenario);
  const vf = v2Verifier(run.verifier);
  return (
    <div className="ev-row" role="button" tabIndex={0} data-unread={ev.unread ? "true" : "false"} onClick={() => onOpen(ev.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(ev.id); } }}>
      {ev.unread && <span className="ev-unread-dot" title="New since you last looked." />}
      <VerdictPill v={run.verdict} detail={run.failNote} />
      <div className="ev-row-main">
        <div className="ev-row-title">
          {ev.title}
          <span className="ev-row-id mono">{ev.run}</span>
        </div>
        <div className="ev-row-meta">
          {vf && <VerifierTag name={vf.name} ver={vf.ver} method={vf.method} current={vf.current} small />}
          {scn && <span className="ev-row-scn mono" title="scenario digest">scn@{scn.digest}</span>}
          {run.skips && run.skips.length > 0 && (
            <span className="ev-skips" title={run.skips.map(s => s.name + " — " + s.reason).join("\n")}>
              {run.skips.length} skip{run.skips.length > 1 ? "s" : ""} · named
            </span>
          )}
          {run.redProof
            ? <span className="ev-red ok" title={run.redProof.note}>red-proofed</span>
            : <span className="ev-red none" title="red_proof_ref: null — a green without a shown red is not yet believed">red_proof_ref: null</span>}
          {!ev.rewatchable && <span className="ev-nocast" title="No terminal cast exists for this run — said out loud, never faked.">rewatchable: false</span>}
          {ev.reads && (
            <span className="ev-reads" title={"Who consumed this record: " + ev.reads.agents + " agent feed fetches, " + ev.reads.people + " page reads."}>
              <Icon name="eye" size={10} /> {ev.reads.agents} agents · {ev.reads.people} people
            </span>
          )}
        </div>
      </div>
      <div className="ev-row-side">
        {run.joined ? <AddressLink addr={run.joined} onOpen={onOpenTrace} dim /> : <span className="addr-none mono">no join</span>}
        <FacetChip facet={run.facet || "manufactured"} small />
        <span className="ev-when mono">{run.when}</span>
      </div>
    </div>
  );
}

function EvidencePage({ repoId, onOpenEvidence, onOpenTrace, embedded }) {
  const [filter, setFilter] = React.useState("all");
  const list = V2_EVIDENCE.filter(ev => {
    const run = v2Run(ev.run) || {};
    if (filter === "all") return true;
    if (filter === "fail") return run.verdict === "fail";
    if (filter === "skips") return (run.skips || []).length > 0;
    if (filter === "no-red") return !run.redProof;
    return true;
  });
  const counts = {
    fail: V2_EVIDENCE.filter(e => (v2Run(e.run) || {}).verdict === "fail").length,
    skips: V2_EVIDENCE.filter(e => ((v2Run(e.run) || {}).skips || []).length > 0).length,
    noRed: V2_EVIDENCE.filter(e => !(v2Run(e.run) || {}).redProof).length,
  };
  return (
    <div className={embedded ? "v2-embed" : "landing landing-page v2-page"}>
      {!embedded && (
      <V2Hero
        plain="Evidence"
        tech="run record → page + feed"
        subtitle="One frozen record per run of this project, rendered twice — a page for people and a feed for agents. Renderings format, never add: nothing here recomputes or editorializes a verdict."
        scope={`${repoId || "jayfarei/opentraces"} · ${V2_EVIDENCE.length} records · ${V2_EVIDENCE.filter(e => e.unread).length} unread · ${counts.noRed} without a red proof`}
      />
      )}
      <div className="v2-filter-row">
        {[
          ["all", "All"], ["fail", `Failing · ${counts.fail}`],
          ["skips", `With skips · ${counts.skips}`], ["no-red", `red_proof_ref: null · ${counts.noRed}`],
        ].map(([k, l]) => (
          <button key={k} className="v2-filter" aria-current={filter === k} onClick={() => setFilter(k)}>{l}</button>
        ))}
      </div>
      <div className="ev-list">
        {list.map(ev => <EvidenceRow key={ev.id} ev={ev} onOpen={onOpenEvidence} onOpenTrace={onOpenTrace} />)}
        {list.length === 0 && <div className="v2-empty">No records match.</div>}
      </div>
      <div className="v2-footnote">
        Every record carries its join stamp back into the ledger through the address grammar
        <span className="mono"> &lt;trace&gt;[:step|:A-B]</span>, and returns to the private home as
        <span className="mono"> manufactured</span> record. Film is never an input to a verdict.
      </div>
    </div>
  );
}

window.EvidencePage = EvidencePage;
