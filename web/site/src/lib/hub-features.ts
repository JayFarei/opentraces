// Page-by-page breakdown of the Hub, shown on /hub. Each entry drives one
// stacked feature card: marketing copy (grounded in what the view actually
// renders) plus the embed params that boot the real chromeless view.
// The live artifact is /hub-preview/index.html?embed=1&view=…

export interface HubFeature {
  id: string;
  kicker: string;
  heading: string;
  body: string;
  bullets: string[];
  /** Logical canvas height (px) for the chromeless view, tuned per feature. */
  height: number;
  /** Logical canvas width (px) override for dense views (e.g. the capsules
   * table is ~1212px wide); defaults to the shared 1020px canvas. */
  canvasWidth?: number;
  /** Renders a bespoke stage component instead of the chromeless iframe embed. */
  custom?: "slicer";
  // Embed params:
  view: string;
  repo?: string;
  dataset?: string;
  child?: string;
  tab?: "conversation" | "trail";
  pr?: string;
}

export interface HubFeatureGroup {
  id: string;
  label: string;
  blurb: string;
  features: HubFeature[];
}

export const HUB_FEATURE_GROUPS: HubFeatureGroup[] = [
  {
    id: "workspace",
    label: "the workspace",
    blurb: "Everything your team has run, in one place.",
    features: [
      {
        id: "overview",
        kicker: "workspace ledger",
        heading: "Every Run In One Ledger",
        body: "A year of agent runs at a glance: a contribution heatmap, token spend by project, dataset sync state, and a trace ledger you can group by day, project, or dataset.",
        bullets: ["heatmap by project or dataset", "token spend per project", "traces tied to hf datasets"],
        height: 720,
        view: "traces-landing",
      },
      {
        id: "projects",
        kicker: "projects",
        heading: "A Trace View of Your Work",
        body: "See what's happening in a codebase right now: branch, open and total traces, last push, the working tree's modified and added files, and the trace in flight.",
        bullets: ["working tree + in-flight trace", "recent traces by day", "datasets and contributors rail"],
        height: 600,
        view: "repo",
        repo: "jayfarei/opentraces",
        child: "traces",
      },
      {
        id: "trace",
        kicker: "inside a trace",
        heading: "Read The Whole Run",
        body: "Replay any run as a conversation: prompts, responses, reasoning, and every tool call inline, with git commits surfaced as checkpoints in the stream.",
        bullets: ["filter by step type", "expand any tool call", "privacy-filtered by default"],
        height: 620,
        view: "trace",
        tab: "conversation",
      },
      {
        id: "trail",
        kicker: "inside a trace",
        heading: "The Run As A Timeline",
        body: "Switch any trace to its trail: a lane-by-lane timeline of the whole run — user turns, planning, reads, edits, and git work — with context-waste and run health flagged inline.",
        bullets: ["phase, git + telemetry lanes", "context-waste flagged inline", "click any step to expand"],
        height: 620,
        view: "trace",
        tab: "trail",
      },
      {
        id: "decomposition",
        kicker: "inside a trace",
        heading: "One Trace, Sliced Four Ways",
        body: "A long session is the sum of several trajectories. The Hub cuts it into usable units with a small library of slicers — a cheap deterministic default plus richer optional grains — over the trace viewer's own step pipe.",
        bullets: ["user-turn + change-burst, deterministic", "milestone + subgoal, one cheap call each", "every cut is a gap-free tiling"],
        height: 0,
        view: "decomposition",
        custom: "slicer",
      },
      {
        id: "pulls",
        kicker: "pull requests",
        heading: "PRs With Intent Alignment",
        body: "Every PR carries the agent trail that built it: which commits are traced, which stated intents the code actually satisfies, and the steps, cost, and wall time behind the diff — reviewed commit by commit.",
        bullets: ["aligned / review / blocked verdicts", "intents satisfied per PR", "cost, steps, agents per branch"],
        height: 760,
        view: "repo",
        repo: "jayfarei/opentraces",
        child: "pulls",
        pr: "128",
      },
    ],
  },
  {
    id: "intelligence",
    label: "trace intelligence",
    blurb: "Read meaning off the whole trace base, not one run at a time.",
    features: [
      {
        id: "spotlight",
        kicker: "conversation intelligence",
        heading: "Ask Your Trace Base",
        body: "Ask anything in plain english. Spotlight reads bodies, reasoning, and tool calls with an LLM grader, then returns ranked traces with scores, snippets, and fingerprints.",
        bullets: ["natural language search", "LLM-graded ranking", "snippet + fingerprint per hit"],
        height: 620,
        view: "spotlight",
      },
      {
        id: "intents",
        kicker: "conversation intelligence",
        heading: "Name The Patterns Agents Fall Into",
        body: "Group your traces by what the agent was actually doing: debug investigations, refactors, tool thrash, spikes. Edit an intent, regrade the window, and pull up every matching trace.",
        bullets: ["editable intent catalogue", "stacked activity over time", "matched traces per intent"],
        height: 600,
        view: "intents",
      },
      {
        id: "evals",
        kicker: "conversation intelligence",
        heading: "Grade Agents Against Your Rules",
        body: "Write the SOPs your agents should follow. An LLM judge grades every run against each rule statement, counts violations, and links you straight to the offending traces.",
        bullets: ["per-SOP violation counts", "stacked violations over time", "open the violating trace"],
        height: 620,
        view: "evals",
      },
      {
        id: "capsules",
        kicker: "shared, replayable traces",
        heading: "Capsules Replay On Main",
        body: "Seal a failing agent session into a replayable capsule, attach it to a repo issue, and re-pose the intent against current main. The verdict moves from fails @sha to passes @main on its own.",
        bullets: ["live verdict vs main", "dependency-version matrix", "scrubbed before sharing"],
        // The capsules table is a wide, data-dense view (~1212px). Render it on a
        // canvas matching its natural width so the right-hand columns don't clip;
        // the frame scales it down uniformly. Height is bumped to keep the rendered
        // card a comparable size at the smaller scale (and show a fuller table).
        height: 760,
        canvasWidth: 1280,
        view: "capsules",
      },
      {
        id: "alerts",
        kicker: "automation",
        heading: "Alerts On Trace Signals",
        body: "Write if/then rules over failure rate, context overflow, tool thrash, cost, and SOP violations. Each fires on a cadence and pipes the offending trace IDs straight to Slack.",
        bullets: ["if/then trace conditions", "per-rule cadence", "one-click slack delivery"],
        height: 600,
        view: "alerts",
      },
      {
        id: "improving",
        kicker: "automation",
        heading: "Heals Itself, On Review",
        body: "When intent spikes or SOP violations cross your thresholds, the agent drafts a fix and opens a PR against the connected repo. Auto-merge stays off, so you stay in the loop.",
        bullets: ["threshold-driven triggers", "PR-only, never main", "heal jobs + cooldowns"],
        height: 620,
        view: "improving",
      },
    ],
  },
  {
    id: "datasets",
    label: "datasets",
    blurb: "Turn the runs worth keeping into datasets you can publish.",
    features: [
      {
        id: "datasets",
        kicker: "datasets",
        heading: "Review Before You Push",
        body: "Your workflow drops new rows into an inbox. Promote the ones worth keeping to staging, skip the rest, then push the batch to a Hugging Face remote.",
        bullets: ["inbox → staging → push", "per-row promote or skip", "local-first, push when ready"],
        height: 600,
        view: "dataset",
        dataset: "ds-edge-traces",
        child: "inbox",
      },
      {
        id: "workflow",
        kicker: "workflows",
        heading: "Security and Privacy, Per Workflow",
        body: "A workflow is the recipe that turns runs into dataset rows. Attach security and privacy tools to it: secret scanning, PII redaction, path anonymization, and your own redaction rules all run before a single row lands.",
        bullets: ["secret + entropy + trufflehog scanning", "PII filter and redaction rules", "scanned before anything publishes"],
        height: 720,
        view: "dataset",
        dataset: "ds-claude-eval-v3",
        child: "workflow",
      },
    ],
  },
];
