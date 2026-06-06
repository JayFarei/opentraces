import SectionRule from "./SectionRule";

const agents = ["claude", "codex", "pi"];

// What capture answers — each question maps to a substrate in the bucket.
const captureQuestions = [
  { q: "what have I done?", a: "trace" },
  { q: "what have I seen?", a: "ctx" },
  { q: "how did my environment change?", a: "trail" },
];

type TreeLine = [glyph: string, name: string, note?: string];

const bucketTree: TreeLine[] = [
  ["", "bucket/"],
  ["├─ ", "manifest.json", "the map"],
  ["├─ ", "traces/v1/", "trace · trail · ctx"],
  ["├─ ", "blobs/v1/", "context · raw"],
  ["└─ ", "events/v1/", "replayable log"],
];

const workflowTabs = ["search API", "security tools", "custom"];
const workflowTree: TreeLine[] = [
  ["", "workflow/"],
  ["├─ ", "SKILL.md"],
  ["├─ ", "schemas/row.schema.json"],
  ["├─ ", "scripts/build_rows.py"],
  ["└─ ", "examples/ · tests/"],
];

const datasetRows = ["pending", "pending", "approved", "pending"];

// Each local stage syncs to its own Hugging Face destination.
const remoteStages = [
  { name: "HF private bucket", verb: "sync" },
  { name: "ML Intern", verb: "run on HF" },
  { name: "Hub dataset", verb: "push" },
];

// Trace consumers — what you build on top of capture + pipeline.
const consumers = [
  {
    tag: "capsule",
    title: "Trace Capsule",
    desc: "Share a real usage episode with a third party — attach the actual agent experience to a GitHub issue, not just a summary of the bug.",
  },
  {
    tag: "skill eval",
    title: "Skill Evaluation",
    desc: "Keep a versioned dataset of skill usage across traces, build a verifier per skill with the OT SDK, and score whether skill changes improve outcomes.",
  },
  {
    tag: "standup",
    title: "Standup",
    desc: "A daily report rebuilt from yesterday's sessions: what was attempted, what landed, what failed, and what's still open before you start today.",
  },
  {
    tag: "spotlight",
    title: "Spotlight",
    desc: "QMD for agent traces. Search your traces mid-session, outside the loop, or for a handoff, so context travels between sessions without planning ahead.",
  },
  {
    tag: "alerts",
    title: "Alerts",
    desc: "Standing alerts and reports over trace usage: failure rate, context waste, third-party tools, secrets, policy violations, or any pattern you care about.",
  },
  {
    tag: "intent pr",
    title: "Intent Pull Request",
    desc: "Walk a PR's commits back to the originating sessions and compile the 'why' alongside the 'how' — intent, lineage, and evidence beside the diff.",
  },
];

function Tree({ lines }: { lines: TreeLine[] }) {
  return (
    <div className="pipe-tree">
      {lines.map(([glyph, name, note], i) => (
        <div key={i} className="pipe-tree-line">
          <span className="g">{glyph}</span>
          <span className={name.endsWith("/") ? "dir" : "fn"}>{name}</span>
          {note && <span className="nt">{"  " + note}</span>}
        </div>
      ))}
    </div>
  );
}

export default function InfraDiagram() {
  return (
    <section>
      <SectionRule label="how it works" />

      <div className="pipeline">
        <div className="pipeline-scroll">
          <div className="pipeline-inner">
            {/* Agent harness — capture answers three questions into the bucket */}
            <div className="pipe-harness">
              <div className="pipe-harness-head">
                <span className="pipe-harness-title">agent harness</span>
                <span className="pipe-harness-chips">
                  {agents.map((a) => (
                    <span key={a} className="pipe-chip">{a}</span>
                  ))}
                </span>
              </div>
              <div className="pipe-harness-cap">capture via hooks</div>
              <div className="pipe-harness-q">
                {captureQuestions.map((it) => (
                  <div key={it.a} className="pipe-q-row">
                    <span className="pipe-q-text">{it.q}</span>
                    <span className="pipe-q-arrow">{"→"}</span>
                    <span className="pipe-q-sub">{it.a}</span>
                  </div>
                ))}
              </div>
              <div className="pipe-harness-write">write {"↓"}</div>
            </div>

            {/* Local stages: bucket → workflow → dataset */}
            <div className="pipe-grid">
              <div className="pipe-card">
                <div className="pipe-card-head"><strong>bucket</strong> private capture store</div>
                <Tree lines={bucketTree} />
              </div>

              <div className="pipe-col-arrow">
                <span className="pipe-arrow-glyph">{"→"}</span>
                <span className="pipe-arrow-lbl">project</span>
              </div>

              <div className="pipe-card">
                <div className="pipe-card-head"><strong>workflow</strong> projects the bucket</div>
                <div className="pipe-tabs">
                  {workflowTabs.map((t) => (
                    <span key={t} className="pipe-tab">{t}</span>
                  ))}
                </div>
                <Tree lines={workflowTree} />
              </div>

              <div className="pipe-col-arrow">
                <span className="pipe-arrow-glyph">{"→"}</span>
                <span className="pipe-arrow-lbl">rows</span>
              </div>

              <div className="pipe-card">
                <div className="pipe-card-head">
                  <strong>dataset</strong> reviewed rows
                  <span className="pipe-inbox">inbox {"✓"}</span>
                </div>
                <div className="pipe-rows">
                  {datasetRows.map((state, i) => (
                    <div key={i} className={`pipe-row${state === "approved" ? " pipe-row-approved" : ""}`}>
                      {state === "approved" && <span className="pipe-row-check">{"✓"}</span>}
                    </div>
                  ))}
                </div>
                <div className="pipe-rows-foot">security tools · regex · entropy</div>
              </div>
            </div>

            {/* Local | Remote boundary */}
            <div className="pipe-boundary">
              <span className="pipe-tag pipe-tag-local">local · your machine</span>
              <span className="pipe-boundary-line" />
              <span className="pipe-tag pipe-tag-remote">remote · hugging face</span>
            </div>

            {/* Remote band — each stage's HF destination, beneath its column */}
            <div className="pipe-remote-band">
              <div className="pipe-remote-grid">
                {remoteStages.map((s, i) => (
                  <div key={s.name} className="pipe-remote-col" style={{ gridColumn: i * 2 + 1 }}>
                    <span className="pipe-cross">{"↓"} {s.verb}</span>
                    <div className="pipe-remote-chip">
                      <span className="pipe-hf-emoji">{"🤗"}</span>
                      <span className="pipe-remote-name">{s.name}</span>
                    </div>
                  </div>
                ))}
              </div>
              <div className="pipe-loop-label">eval · training · scoring</div>
            </div>
          </div>
        </div>
      </div>

      {/* Trace consumers — proof of value */}
      <div className="pipe-proof-caption">trace consumers</div>
      <p className="pipe-proof-sub">
        Traces are not only logs. With capture and pipeline in place, they become retained evidence you can
        search, secure, share, evaluate, and turn into new workflows.
      </p>
      <div className="use-grid use-grid-consumers" style={{ marginTop: 20 }}>
        {consumers.map((c) => (
          <div key={c.tag} className="use-card">
            <div className="use-card-tag">{c.tag}</div>
            <h4>{c.title}</h4>
            <p>{c.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
