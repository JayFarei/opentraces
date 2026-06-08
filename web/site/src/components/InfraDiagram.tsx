import type { ComponentType, ReactNode } from "react";
import SectionRule from "./SectionRule";
import { ClaudeGlyph, CodexGlyph, PiGlyph } from "./AgentGlyphs";
import HuggingFaceLogo from "./HuggingFaceLogo";
import { EyeIcon, EditIcon, AnchorIcon, LineageIcon } from "./SessionIcons";
import TraceMinimap from "./TraceMinimap";
import CommitTree from "./CommitTree";
import {
  CapsuleIcon,
  EvalIcon,
  StandupIcon,
  SpotlightIcon,
  AlertIcon,
  PrIcon,
} from "./ConsumerIcons";

// What the trace colors mean — the action vocabulary, in canonical order.
const TRACE_LEGEND = [
  { c: "var(--c-user)", label: "prompt" },
  { c: "var(--c-think)", label: "reasoning" },
  { c: "var(--c-read)", label: "context" },
  { c: "var(--c-exec)", label: "command" },
  { c: "var(--c-write)", label: "edit" },
];

// One station in the session story (sees → does → changes → lasts → lineage).
function Station({
  icon,
  title,
  sub,
  agent,
}: {
  icon?: ReactNode;
  title: string;
  sub: string;
  agent?: boolean;
}) {
  return (
    <div className={`pipe-station${agent ? " pipe-station-agent" : ""}`}>
      <div className="pipe-station-ico">
        {agent ? (
          <span className="pipe-station-agents">
            <ClaudeGlyph className="pipe-station-glyph" />
            <CodexGlyph className="pipe-station-glyph" />
            <PiGlyph className="pipe-station-glyph" />
          </span>
        ) : (
          icon
        )}
      </div>
      <div className="pipe-station-t">{title}</div>
      <div className="pipe-station-s">{sub}</div>
    </div>
  );
}

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
// Two real remote destinations, aligned beneath the bucket and dataset columns.
// (The "ML Intern" node was README ASCII shorthand with no shipped command.)
const remoteStages = [
  { name: "HF private bucket", verb: "sync", note: "synchronize a private bucket" },
  { name: "Hub dataset", verb: "publish", note: "publish, private or public" },
];

// Beyond training — what you build on top of capture + pipeline.
// Each card is identified by an icon describing the workflow, not a color.
type Consumer = {
  Icon: ComponentType<{ className?: string }>;
  title: string;
  desc: string;
};
const consumers: Consumer[] = [
  {
    Icon: CapsuleIcon,
    title: "Trace Capsule",
    desc: "Share a real usage episode with a third party — attach the actual agent experience to a GitHub issue, not just a summary of the bug.",
  },
  {
    Icon: EvalIcon,
    title: "Skill Evaluation",
    desc: "Keep a versioned dataset of skill usage across traces, build a verifier per skill with the OT SDK, and score whether skill changes improve outcomes.",
  },
  {
    Icon: StandupIcon,
    title: "Standup",
    desc: "A daily report rebuilt from yesterday's sessions: what was attempted, what landed, what failed, and what's still open before you start today.",
  },
  {
    Icon: SpotlightIcon,
    title: "Spotlight",
    desc: "QMD for agent traces. Search your traces mid-session, outside the loop, or for a handoff, so context travels between sessions without planning ahead.",
  },
  {
    Icon: AlertIcon,
    title: "Alerts",
    desc: "Standing alerts and reports over trace usage: failure rate, context waste, third-party tools, secrets, policy violations, or any pattern you care about.",
  },
  {
    Icon: PrIcon,
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
            {/* Agent harnesses — what we capture in every session: what the agent
                sees and does, what it changes, and which of those changes last. */}
            <div className="pipe-harness">
              <div className="pipe-harness-head">
                <span className="pipe-harness-title">agent harnesses</span>
                <span className="pipe-harness-note">captured in every session</span>
              </div>
              <div className="pipe-session-flow">
                <div className="pipe-session">
                  <span className="pipe-session-tag">session</span>
                  <div className="pipe-stations">
                    <Station icon={<EyeIcon />} title="what it sees" sub="context · ctx" />
                    <span className="pipe-station-arrow" aria-hidden="true">{"→"}</span>
                    <Station agent title="what it does" sub="the agent · trace" />
                    <span className="pipe-station-arrow" aria-hidden="true">{"→"}</span>
                    <Station icon={<EditIcon />} title="what it changes" sub="environment · trail" />
                  </div>
                  <div className="pipe-trace-block">
                    <div className="pipe-trace-row">
                      <span className="pipe-trace-label">trace</span>
                      <TraceMinimap />
                    </div>
                    <div className="pipe-trace-legend">
                      {TRACE_LEGEND.map((l) => (
                        <span key={l.label} className="pipe-leg">
                          <span className="pipe-leg-sw" style={{ background: l.c }} />
                          {l.label}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
                <span className="pipe-station-arrow pipe-station-arrow-cross" aria-hidden="true">{"→"}</span>
                <div className="pipe-after">
                  <div className="pipe-after-row">
                    <Station icon={<AnchorIcon />} title="what lasts" sub="in git history" />
                    <span className="pipe-station-arrow" aria-hidden="true">{"→"}</span>
                    <Station icon={<LineageIcon />} title="lineage" sub="survives git history" />
                  </div>
                  <div className="pipe-commit-block">
                    <div className="pipe-commit-row">
                      <span className="pipe-trace-label">commits</span>
                      <CommitTree />
                    </div>
                    <div className="pipe-commit-legend">
                      <span className="pipe-leg"><span className="pipe-cdot" />survives</span>
                      <span className="pipe-leg"><span className="pipe-cdot pipe-cdot-hollow" />reverted</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Captured data drops from the harness down into the bucket —
                the start of one continuous rainbow line through to the dataset. */}
            <div className="pipe-drop" aria-hidden="true">
              <span className="pipe-drop-cell">
                <span className="pipe-drop-line" />
                <span className="pipe-drop-arrow" />
              </span>
            </div>

            {/* Local stages: bucket → workflow → dataset. Each gap carries its
                own full rainbow connector that flows toward the next stage. */}
            <div className="pipe-grid">
              <div className="pipe-card">
                <div className="pipe-card-head"><strong style={{ color: "var(--c-read)" }}>bucket</strong> private evidence store</div>
                <Tree lines={bucketTree} />
              </div>

              <div className="pipe-col-arrow" aria-hidden="true">
                <span className="pipe-col-line" />
                <span className="pipe-col-chevron" />
                <span className="pipe-col-lbl">reads</span>
              </div>

              <div className="pipe-card">
                <div className="pipe-card-head"><strong style={{ color: "var(--c-exec)" }}>workflow</strong> dataset as code</div>
                <div className="pipe-tabs">
                  {workflowTabs.map((t) => (
                    <span key={t} className="pipe-tab">{t}</span>
                  ))}
                </div>
                <Tree lines={workflowTree} />
              </div>

              <div className="pipe-col-arrow" aria-hidden="true">
                <span className="pipe-col-line" />
                <span className="pipe-col-chevron" />
                <span className="pipe-col-lbl">builds</span>
              </div>

              <div className="pipe-card">
                <div className="pipe-card-head">
                  <strong style={{ color: "var(--c-push)" }}>dataset</strong> reviewed rows
                  <span className="pipe-inbox">inbox {"✓"}</span>
                </div>
                <div className="pipe-rows">
                  {datasetRows.map((state, i) => (
                    <div key={i} className={`pipe-row${state === "approved" ? " pipe-row-approved" : ""}`}>
                      {state === "approved" && <span className="pipe-row-check">{"✓"}</span>}
                    </div>
                  ))}
                </div>
                <div className="pipe-rows-foot">reviewed · approved rows only</div>
              </div>

              {/* Workflow + dataset are one unit — no dataset without its workflow. */}
              <div className="pipe-pair-bracket">a dataset is built &amp; kept current by its workflow</div>
            </div>

            {/* Security boundary — local above the line, remote below it, with
                security screening on the crossing. */}
            <div className="pipe-security">
              <span className="pipe-zone pipe-zone-local">local</span>
              <div className="pipe-security-core">
                <span className="pipe-security-rail" aria-hidden="true" />
                <span className="pipe-security-head">security screening tools</span>
                <span className="pipe-security-rail" aria-hidden="true" />
              </div>
              <span className="pipe-zone pipe-zone-remote">remote</span>
            </div>

            {/* Remote zone — each local stage's optional Hugging Face destination */}
            <div className="pipe-remote-band">
              <div className="pipe-remote-grid">
                {remoteStages.map((s) => (
                  <div key={s.name} className="pipe-remote-col">
                    <span className="pipe-cross">{"↓"} {s.verb}</span>
                    <div className="pipe-remote-chip">
                      <HuggingFaceLogo size={22} className="pipe-remote-logo" />
                      <span className="pipe-remote-name">{s.name}</span>
                      <span className="pipe-remote-via">{s.note}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

          </div>
        </div>
      </div>

      {/* Beyond training — what the trace infrastructure unlocks */}
      <div className="pipe-proof-caption">beyond training</div>
      <p className="pipe-proof-sub">
        Training data is just one use. Once your trace infrastructure is in place, you can leverage it to
        fuel many more workflows.
      </p>
      <div className="use-grid use-grid-consumers" style={{ marginTop: 20 }}>
        {consumers.map((c) => (
          <div key={c.title} className="use-card use-card-consumer">
            <div className="use-card-head">
              <span className="use-card-ico"><c.Icon /></span>
            </div>
            <h4>{c.title}</h4>
            <p>{c.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
