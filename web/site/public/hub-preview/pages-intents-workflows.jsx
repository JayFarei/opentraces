// Arena builder — the classifier registry and the loop that trains them.
//
// One registry shows every classifier (system defaults + yours), how each
// was trained, and how it performs on the sliced data (coverage of the
// slice stream, precision against human labels). The page's four verbs:
// build a dataset, build an arena, create a classifier, improve one.

const WORKFLOW_BLOCKS = [
  { id: "heuristic", name: "Heuristic", desc: "regex / step-pattern rules, runs at capture" },
  { id: "local-model", name: "Local model", desc: "small labeller (qwen3-8b class), on-device" },
  { id: "embedding", name: "Embedding cluster", desc: "nearest-cluster match over slice embeddings" },
  { id: "outcome", name: "Outcome check", desc: "verify/ship signals from the slice" },
  { id: "byo", name: "Bring your own", desc: "point at any GGUF / endpoint you host" },
];

// Every classifier known to the workspace, with performance on sliced data.
const CLASSIFIERS = [
  { id: "slicer", name: "milestone slicer", origin: "system", out: { kind: "label", label: "slices" }, model: "qwen3-4b · local", trained: "shipped v3", coverage: 1.0, precision: 0.97 },
  { id: "stages", name: "process stages", origin: "system", out: { kind: "label", label: "stage" }, model: "deterministic + qwen3-4b", trained: "shipped v3", coverage: 1.0, precision: 0.94 },
  { id: "outcome", name: "outcome check", origin: "system", out: { kind: "label", label: "outcome" }, model: "deterministic", trained: "shipped v3", coverage: 1.0, precision: 0.99 },
  { id: "grader", name: "family grader", origin: "system", out: { kind: "label", label: "family" }, model: "qwen3-8b · local", trained: "regraded 1h ago · 312 human labels", coverage: 0.87, precision: 0.81, improvable: true },
  { id: "wf-thrash", name: "thrash-detector", origin: "yours", out: { kind: "label", label: "@Tool thrash" }, model: "heuristic → qwen3-8b", trained: "3d ago · 88 labels", coverage: 0.12, precision: 0.92, improvable: true, toggle: true, on: true },
  { id: "wf-flaky", name: "flaky-test-hunter", origin: "yours", out: { kind: "arena", label: "arena queue" }, model: "embedding → outcome", trained: "5d ago · 41 labels", coverage: 0.06, precision: 0.88, improvable: true, toggle: true, on: true },
  { id: "wf-redact", name: "redaction-probe", origin: "yours", out: { kind: "trace", label: "probe trace" }, model: "redact-scan.gguf", trained: "never — needs labels", coverage: 0, precision: null, improvable: true, toggle: true, on: false },
];

function ClassifierRegistry({ onImprove }) {
  const [enabled, setEnabled] = React.useState(() => Object.fromEntries(CLASSIFIERS.filter((c) => c.toggle).map((c) => [c.id, c.on])));
  return (
    <section className="wf-card" data-screen-label="classifier-registry">
      <header className="ihm-head">
        <div>
          <div className="icc-kicker">Classifiers</div>
          <div className="icc-sub">Everything labeling the slice stream — the system's defaults and the ones you trained — measured on the sliced data itself.</div>
        </div>
      </header>
      <div className="creg">
        <div className="creg-h">
          <span>Classifier</span><span>Output</span><span>Trained</span>
          <span className="num">Coverage</span><span className="num">Precision</span><span />
        </div>
        {CLASSIFIERS.map((c) => {
          const off = c.toggle && !enabled[c.id];
          return (
            <div key={c.id} className={"creg-row" + (off ? " off" : "")}>
              <span className="creg-name">
                {c.toggle ? (
                  <button
                    className={"wf-toggle" + (enabled[c.id] ? " on" : "")}
                    role="switch" aria-checked={!!enabled[c.id]}
                    title={enabled[c.id] ? "Pause" : "Resume"}
                    onClick={() => setEnabled((e) => ({ ...e, [c.id]: !e[c.id] }))}
                  ><span className="wf-knob" /></button>
                ) : (
                  <span className="dc-badge">system</span>
                )}
                <span className="mono">{c.name}</span>
                <span className="creg-model">{c.model}</span>
              </span>
              <span className={"wf-out " + c.out.kind}>
                {c.out.kind === "label" && <Icon name="tag" size={10} />}
                {c.out.kind === "arena" && <Icon name="arena" size={10} />}
                {c.out.kind === "trace" && <Icon name="activity" size={10} />}
                {c.out.label}
              </span>
              <span className="creg-trained">{c.trained}</span>
              <span className="num">
                <span className="creg-bar"><i style={{ width: (c.coverage * 100) + "%" }} /></span>
                <span className="mono">{Math.round(c.coverage * 100)}%</span>
              </span>
              <span className="num mono">{c.precision == null ? "—" : c.precision.toFixed(2)}</span>
              <span className="creg-act">
                {c.improvable && (
                  <button className="rfq-btn" onClick={() => onImprove && onImprove(c)} title="Route your labels to this classifier and retrain">Improve</button>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ── train / improve a classifier: compose blocks + label slices ─────
function TrainClassifier({ slices, onOpen, target }) {
  return (
    <section className="wf-card" data-screen-label="train-classifier" id="train-classifier">
      <header className="ihm-head">
        <div>
          <div className="icc-kicker">{target ? "Improve a classifier" : "Train a classifier"}</div>
          <div className="icc-sub">
            {target
              ? <>Improving <span className="mono">{target.name}</span> — your labels below are routed to its next training pass ({target.trained}).</>
              : "Two inputs: compose a workflow from blocks, and label slices — your labels become its training set."}
          </div>
        </div>
        <ToolBtn icon="plus" label="New classifier" primary />
      </header>
      {!target && (
        <>
          <div className="wf-builder-k">1 · Compose — blocks from the hub, or publish your own</div>
          <div className="wf-builder-blocks">
            {WORKFLOW_BLOCKS.map((b) => (
              <button key={b.id} className="wf-block" title={b.desc}>
                <span className="wf-block-name">{b.name}</span>
                <span className="wf-block-desc">{b.desc}</span>
              </button>
            ))}
          </div>
          <div className="wf-builder-note">Chain blocks left to right; end with an output — label a family, emit a trace, or queue for an arena. Workflows run locally against the slice stream.</div>
        </>
      )}
      <div className="wf-builder-k train-k">{target ? "Label — routed to " + target.name : "2 · Label — low-confidence matches, backfilled as you go"}</div>
      <RefineQueue slices={slices} onOpen={onOpen} bare />
    </section>
  );
}

Object.assign(window, { ClassifierRegistry, TrainClassifier, CLASSIFIERS, WORKFLOW_BLOCKS });
