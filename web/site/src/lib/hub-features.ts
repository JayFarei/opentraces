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
  /** v2 deep-links: artifact page, evidence record, capsule detail, bench inner tab. */
  artifact?: string;
  evidence?: string;
  capsule?: string;
  benchtab?: "atlas" | "evidence" | "runs";
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
    blurb: "Every run your team has made, in one place.",
    features: [
      {
        id: "overview",
        kicker: "workspace ledger",
        heading: "Every Run In One Ledger",
        body: "The Hub opens on activity, spend, and the ledger it all stands on: a year of runs as a contribution heatmap, token distribution across projects and models, and the traces underneath. Click a day in the heatmap and the ledger filters to it.",
        bullets: [
          "heatmap by volume, project or dataset",
          "tokens by project, model or harness",
          "group the ledger by day, project or dataset",
        ],
        height: 760,
        view: "traces-landing",
      },
      {
        id: "traces",
        kicker: "every trace",
        heading: "Find The Session You Half Remember",
        body: "The full ledger is built for recall. Scrub the day timeline to pin what happened on a given day, narrow by project, dataset or harness, then page through the whole history.",
        bullets: [
          "day timeline, click a bar to pin it",
          "filter by project, dataset or harness",
          "counts on every filter value",
        ],
        height: 700,
        view: "traces-index",
      },
      {
        id: "trace",
        kicker: "inside a trace",
        heading: "Read The Whole Run",
        body: "Replay any run as a conversation: prompts, responses, reasoning, and every tool call inline, with git commits surfaced as checkpoints in the stream. A privacy panel shows what was filtered out before you ever saw it.",
        bullets: ["filter by step type", "expand any tool call", "privacy-filtered by default"],
        height: 620,
        view: "trace",
        tab: "conversation",
      },
      {
        id: "trail",
        kicker: "inside a trace",
        heading: "The Run As A Timeline",
        body: "The same run, read differently: lanes for user, plan, think, read, exec and write, beside working, local and remote git columns and per-step tokens, duration and context fill. Context-waste and run-health signals sit in the gutter, and the panel says whether the numbers came off the wire or off the record.",
        bullets: [
          "phase, git and telemetry lanes",
          "context-waste flagged inline",
          "wire-fidelity or record, stated",
        ],
        height: 620,
        view: "trace",
        tab: "trail",
      },
      {
        id: "decomposition",
        kicker: "inside a trace",
        heading: "One Trace, Sliced Four Ways",
        body: "A long session is the sum of several trajectories. The Hub cuts it into usable units with a small library of slicers: two deterministic grains cut at capture, two labeled by a small model running locally. Every cut is a gap-free tiling of the same steps.",
        bullets: [
          "user-turn and change, deterministic",
          "milestone and sub-goal, labeled locally",
          "the slicer that cut a row travels with it",
        ],
        // Bespoke stage, never boots the iframe. `view` still has to name a
        // view the app handles so `sync-hub check` stays honest.
        height: 0,
        view: "trace",
        custom: "slicer",
      },
    ],
  },
  {
    id: "projects",
    label: "projects & pull requests",
    blurb: "Each codebase, and the agent work that landed in it.",
    features: [
      {
        id: "repo-overview",
        kicker: "projects",
        heading: "What This Codebase Has Been Up To",
        body: "Every project opens on its own pulse: sessions this week, tokens and cost for the month, the session in flight, and a four-week session map with one lane per harness. A flow diagram then follows those sessions to where they ended up, in datasets, in pull request trails, or retained in the bucket.",
        bullets: [
          "28-day session map, one lane per harness",
          "sessions traced to their destination",
          "open PRs, datasets fed, harness mix",
        ],
        height: 760,
        view: "repo",
        repo: "jayfarei/opentraces",
        child: "overview",
      },
      {
        id: "pulls",
        kicker: "pull requests",
        heading: "A Change Gate On Every PR",
        body: "Open a pull request and the bench's change gate sits above the review: the evidence pack behind it, the guarantees the change touches, and any touched surface that maps to no guarantee, named as surface-drift instead of passing quietly. Below it, the agent trail that built the branch, commit by commit, with intents satisfied, cost, steps and wall time.",
        bullets: [
          "evidence pack of frozen runs",
          "unmapped surfaces named, never silent",
          "intent alignment per commit",
        ],
        height: 760,
        view: "repo",
        repo: "jayfarei/opentraces",
        child: "pulls",
        pr: "128",
      },
    ],
  },
  {
    id: "bench",
    label: "the bench",
    blurb: "One bench per project: does the product still do what you promised?",
    features: [
      {
        id: "atlas",
        kicker: "the bench",
        heading: "Did The Promises Hold?",
        body: "The atlas is one row per product guarantee, written in product language: the scenario bound to it, the verifier that judges it, the last verdict, and how fresh that verdict is. Every hole is a named state, unbound, stale-run, stale-verifier, no-red-proof, rather than a blank cell.",
        bullets: [
          "change gate and release gate side by side",
          "six named row states, no blanks",
          "world signals arrive as a second, later label",
        ],
        // The atlas table is wide (six columns), so it renders on its natural
        // 1280px canvas and scales down. Height is bumped to match: at the
        // smaller scale this still lands about the size of a 700px card, and
        // it clears the two gate panels to show real guarantee rows.
        height: 860,
        canvasWidth: 1280,
        view: "repo",
        repo: "jayfarei/opentraces",
        child: "bench",
        benchtab: "atlas",
      },
      {
        id: "bench-runs",
        kicker: "the bench",
        heading: "Scenario, Verifier, Disposable Box",
        body: "Each run pairs a scenario with a specific verifier version, executes it on a leased box, and freezes one record. Skips are listed by name, and a green whose verifier was never shown failing carries red_proof_ref: null instead of being counted as proof.",
        bullets: [
          "verdict, box and sandbox tier per run",
          "skips named, never silently passed over",
          "a green with no red proof is marked, not counted",
        ],
        height: 720,
        view: "repo",
        repo: "jayfarei/opentraces",
        child: "bench",
        benchtab: "runs",
      },
      {
        id: "evidence",
        kicker: "the bench",
        heading: "Rewatch The Run That Proved It",
        body: "Every frozen run record renders twice: a page for people, with a scrubbable terminal cast, the red proof, the scorecard and the named skips, and the same record as a JSON feed for agents. When no cast exists the record says rewatchable: false rather than faking one.",
        bullets: [
          "scrub the cast, red proof marked on the track",
          "the same record as a feed for agents",
          "annotations sit beside the record, never inside it",
        ],
        height: 760,
        canvasWidth: 1280,
        view: "evidence-detail",
        evidence: "ev-201",
      },
    ],
  },
  {
    id: "projections",
    label: "projections",
    blurb: "Everything you can derive from an app and the work that built it.",
    features: [
      {
        id: "projections-index",
        kicker: "projections",
        heading: "Datasets Are Static, Arenas Play",
        body: "One table for everything projected out of an application and its agentic work. Datasets are static, rows materialized by a workflow; arenas are stateful, where a bench plays a scenario, a capsule plays a trace, and a gym plays a task family. A bench lives in its project, so its row links back home.",
        bullets: [
          "filter by kind, search by name",
          "rows, inbox, pass rate at a glance",
          "benches link back to their project",
        ],
        height: 660,
        view: "projections-index",
      },
      {
        id: "dataset",
        kicker: "datasets",
        heading: "Review Before You Push",
        body: "A dataset is local first. Its workflow drops candidate rows into an inbox, you promote the ones worth keeping to staging, and the batch goes to a Hugging Face remote when you say so. Each candidate carries the slice of the source trace it was cut from.",
        bullets: [
          "inbox, then staging, then push",
          "per-row promote or skip",
          "slice provenance on every candidate",
        ],
        height: 640,
        view: "dataset",
        dataset: "ds-edge-traces",
        child: "inbox",
      },
      {
        id: "capsule",
        kicker: "capsules",
        heading: "Sealed Runs That Travel",
        body: "A capsule is one failing episode, sealed. Anyone can witness the film straight from the URL with no account, or step through the sealed conversation and trail. The badge is the honest part: today every capsule refuses the reproducible claim and reports floor trust instead of implying a live verdict.",
        bullets: [
          "witness anonymously, reenacting is gated",
          "every dependency mode stated, not assumed",
          "swap model, harness or provider to pose a counterfactual",
        ],
        height: 760,
        canvasWidth: 1280,
        view: "capsule-detail",
        capsule: "cap-1",
      },
    ],
  },
  {
    id: "ops",
    label: "intelligence & ops",
    blurb: "Read meaning off the whole trace base, and see what it costs.",
    features: [
      {
        id: "spotlight",
        kicker: "trace intelligence",
        heading: "Ask Your Trace Base",
        body: "Ask anything in plain english. Spotlight reads bodies, reasoning and tool calls with an LLM grader, then returns ranked traces with a score, a snippet, and the run's fingerprint.",
        bullets: ["natural language search", "LLM-graded ranking", "snippet and fingerprint per hit"],
        height: 620,
        view: "spotlight",
      },
      {
        id: "boxes",
        kicker: "metering",
        heading: "Boxes And AI, Metered",
        body: "Boxes bill by the hour per environment and AI credits are spent per model call. Two meters track the month against its allowance, with every lease and model call itemized underneath and daily lease hours stacked by environment, model spend drawn over them.",
        bullets: [
          "box credits and AI credits against allowance",
          "every lease and model call itemized",
          "7, 30 or 90 days, exportable as CSV",
        ],
        height: 720,
        view: "boxes",
      },
      {
        id: "settings",
        kicker: "setup & security",
        heading: "What Is Allowed To Leave Your Machines",
        body: "One page for the whole setup: the Hugging Face identity everything runs under, every machine mirroring its local bucket into a single private remote with its own digest and sync state, and the scrubbing pipeline that has to pass before anything syncs.",
        bullets: [
          "machines inferred from pushes, no central registry",
          "off, basic, recommended or strict presets",
          "global redaction strings, applied before review",
        ],
        height: 720,
        view: "settings",
      },
    ],
  },
];
