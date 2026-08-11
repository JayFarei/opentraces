// Parameterizer (horizon) — classifiers over slices; harvests tasks and
// scenarios from real work. Was "Arenas": arena is the family name and
// never a surface. Classifier trust states are mechanically derived and
// human-gated at the top — the ladder shows, never a bare precision number.
//
// Structure:
//   1. Default classifiers — the system's invariants (slicer, process
//      stages, outcome, family grader).
//   2. Your classifiers — workflows you composed; label families, emit
//      traces, queue tasks for an arena.
//   3. Train a classifier — compose a workflow from blocks AND label
//      slices; your labels are its training set.
//   4. Task families × process — what the classifiers produce; drill in,
//      then promote a family into a dataset or an arena.

function ParameterizerPage({ onSelectTrace, onOpenBench }) {
  const matched = React.useMemo(() => buildMatched(INTENTS, RECENT_TRACES, 91), []);
  const allSlices = React.useMemo(
    () => INTENTS.flatMap((it) =>
      buildMatchedSlices(it, matched[it.id] || []).map((s) => ({ ...s, intentId: it.id, intentName: it.name }))
    ),
    [matched]
  );
  const [sel, setSel] = React.useState({ id: null, type: null });
  const [promoted, setPromoted] = React.useState(null);
  const [improveTarget, setImproveTarget] = React.useState(null);
  React.useEffect(() => { setPromoted(null); }, [sel.id]);

  const active = sel.id ? (INTENTS.find((i) => i.id === sel.id) || null) : null;
  const activeSlices = active ? allSlices.filter((s) => s.intentId === active.id) : allSlices;
  const diagIntent = active || { id: "all", name: "all slices", count: allSlices.length, color: "var(--fg-mute)" };

  return (
    <div className="landing landing-page">
      <PageHero
        kicker="Bench · horizon"
        title="Parameterizer"
        subtitle="Harvests tasks and scenarios from real slices. Classifiers cluster task-specific slices into families; a ratified family becomes a bench scenario or a gym task. Horizon surface — the loop below runs on observed data, nothing here is manufactured."
        scope={`last 24h · 6 projects · 482 sessions · ${allSlices.length} matched slices`}
        actions={
          <div className="ph-action-group">
            <ToolBtn icon="datasets" label="Build dataset" />
            <ToolBtn icon="terminal" label="Draft bench scenario" onClick={onOpenBench} />
            <ToolBtn icon="tool" label="Improve classifier" onClick={() => setImproveTarget(CLASSIFIERS.find((c) => c.id === "grader"))} />
            <ToolBtn icon="plus" label="New classifier" primary onClick={() => setImproveTarget(null)} />
          </div>
        }
      />

      <ClassifierRegistry onImprove={setImproveTarget} />
      <TrainClassifier slices={allSlices} onOpen={onSelectTrace} target={improveTarget} />

      <IntentHeatmap matched={matched} sel={sel} onSel={setSel} />

      <section className="intel-matched-card intent-inspector" data-screen-label="intent-inspector">
        <header className="icc-head">
          <div>
            <div className="icc-kicker">Inspect</div>
            <div className="icc-sub icc-crumbs">
              <button className={"icc-crumb" + (!active ? " here" : "")} onClick={() => setSel({ id: null, type: null })}>
                All slices <span className="mono">{allSlices.length}</span>
              </button>
              {active && (
                <>
                  <span className="crumb-sep">›</span>
                  <span className="icc-crumb here">
                    <span className="ilc-dot inline" style={{ background: active.color }} />
                    <span className="mono">@{active.name}</span>
                  </span>
                </>
              )}
              {sel.type && (
                <>
                  <span className="crumb-sep">›</span>
                  <span className="icc-crumb here">
                    <SliceGlyph type={sel.type} size={10} /> {SLICE_TYPES[sel.type].name}
                    <button className="crumb-x" onClick={() => setSel({ id: sel.id, type: null })} title="Drill up to the whole cluster">✕</button>
                  </span>
                </>
              )}
            </div>
          </div>
          {active && (
            <div className="ph-action-group">
              {promoted ? (
                <span className="promote-done"><Icon name="check" size={12} /> {promoted}</span>
              ) : (
                <>
                  <ToolBtn icon="datasets" label="Promote to dataset" onClick={() => setPromoted(`Cluster promoted — dataset ${active.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")} drafted from ${activeSlices.length} slices, rows content-addressed.`)} />
                  <ToolBtn icon="terminal" label="Promote to bench scenario" onClick={() => setPromoted(`Cluster promoted — bench scenario drafted: repo pins + verify commands from ${activeSlices.length} slices become scenario steps.`)} />
                </>
              )}
            </div>
          )}
        </header>
        <IntentDiagnostics intent={diagIntent} slices={activeSlices} />
        <IntentSlicesPanel
          intent={diagIntent}
          slicesOverride={activeSlices}
          onSelectTrace={onSelectTrace}
          presetType={sel.type}
          allMode={!active}
        />
      </section>
    </div>
  );
}

window.ParameterizerPage = ParameterizerPage;
