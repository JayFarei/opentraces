"use client";

import Link from "next/link";
import { useState } from "react";
import Terminal from "./Terminal";
import { AGENT_PROMPT } from "@/lib/agent-prompt";
import pkg from "@/lib/version.json";
import type { HeroMetricItem } from "@/lib/homepage-metrics";

const tabLabels = ["setup", "bucket", "trace", "trail", "ctx", "workflow", "dataset"];
const AGENT_LINES = AGENT_PROMPT.split("\n").length;

const installMethods = [
  { label: "pipx", cmd: "pipx install opentraces", copyText: "pipx install opentraces" },
  { label: "brew", cmd: "brew install [..] opentraces", copyText: "brew install JayFarei/opentraces/opentraces" },
  { label: "skill", cmd: "npx skills add jayfarei/opentraces", copyText: "npx skills add jayfarei/opentraces" },
  { label: "pi", cmd: "pi install npm:opentraces-pi", copyText: "pi install npm:opentraces-pi" },
  { label: "agent", cmd: `agent setup prompt +${AGENT_LINES} lines`, copyText: AGENT_PROMPT, prefix: ">" },
];

function MetricIcon({ icon }: { icon: HeroMetricItem["icon"] }) {
  if (icon === "install") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M8 2l5 2.5v5L8 12 3 9.5v-5L8 2Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
        <path d="M3 4.5L8 7l5-2.5M8 7v5" fill="none" stroke="currentColor" strokeWidth="1.1" />
      </svg>
    );
  }

  if (icon === "download") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M8 2v7m0 0 3-3m-3 3L5 6M3 12.5h10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="square" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M3 4.5h10M3 8h10M3 11.5h10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="square" />
      <path d="M5 3v10M11 3v10" fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinecap="square" />
    </svg>
  );
}

// ── Unified tab panel model ──────────────────────────────────
// Every tab renders the same shape: a prompt+command line, a framed
// rail panel (pill header · subtitle · aligned rows · footer note),
// then one or two suggested next commands. Tab content differs, the
// structure never does.
type PanelRow = {
  label: string;
  value: string;
  valCls?: string; // di (default) · f · n · s · w · c
  status?: string;
  statusCls?: string; // ok (default) · di · w
};

type Panel = {
  prompt: string;
  command: string;
  pill: string;
  subtitle: string;
  rows: PanelRow[];
  footer: string;
  next: string[];
};

const HERO_PANELS: Panel[] = [
  {
    prompt: "~$",
    command: "opentraces setup",
    pill: "opentraces",
    subtitle: `machine setup · v${pkg.version}`,
    rows: [
      { label: "agents", value: "claude · codex · pi", valCls: "s", status: "ready" },
      { label: "hooks", value: "session capture", status: "on" },
      { label: "git + watcher", value: "post-commit · blame", status: "on" },
      { label: "skill", value: "shared agent skill", status: "linked" },
      { label: "hf login", value: "alice-dev", valCls: "s", status: "auth" },
    ],
    footer: "Global tracking on — repos auto-enroll on first capture",
    next: ["code as usual — no per-repo init needed", "opentraces bucket status"],
  },
  {
    prompt: "~/my-project$",
    command: "opentraces bucket status",
    pill: "bucket",
    subtitle: "private capture store",
    rows: [
      { label: "traces", value: "128 envelopes", valCls: "f", status: "local" },
      { label: "events", value: "42 batches", valCls: "f", status: "replayable" },
      { label: "blobs", value: "1.8 GB", valCls: "n", status: "verified" },
      { label: "agents", value: "claude · codex · pi", valCls: "s", status: "merged" },
      { label: "remote", value: "alice/opentraces-bucket", valCls: "s", status: "manual", statusCls: "di" },
    ],
    footer: "Raw evidence stays private until you sync",
    next: ["opentraces trace query --since 7d", "opentraces bucket remote push"],
  },
  {
    prompt: "~/my-project$",
    command: "opentraces trace query --since 7d",
    pill: "trace",
    subtitle: "search · map · slice",
    rows: [
      { label: "candidates", value: "12 found", valCls: "n", status: "index" },
      { label: "tr_7f31", value: "fix billing webhook", valCls: "s", status: "alive_on_path" },
      { label: "tr_c42a", value: "refactor auth middleware", valCls: "s", status: "alive_transformed" },
      { label: "tr_91bb", value: "add settings page", valCls: "s", status: "unknown", statusCls: "w" },
      { label: "facets", value: "skill · files · survival", status: "bounded", statusCls: "di" },
    ],
    footer: "Bounded packets — no full transcript load",
    next: ["opentraces trace map tr_7f31 --bursts", "opentraces trace slice tr_7f31 --template bursts"],
  },
  {
    prompt: "~/my-project$",
    command: "opentraces trail blame commit ac01917",
    pill: "trail",
    subtitle: "git-anchored lineage",
    rows: [
      { label: "commit", value: "feat(viewer): hover actions", valCls: "f", status: "ac01917", statusCls: "di" },
      { label: "s:6606fc1f", value: "server, graph_api +2", valCls: "w", status: "+8 ~12" },
      { label: "s:fe8fe3fd", value: "PushModal", valCls: "w", status: "+6" },
      { label: "s:5baac494", value: "ReviewView +1", valCls: "w", status: "+7" },
      { label: "coverage", value: "3 sessions", valCls: "n", status: "100%" },
    ],
    footer: "Every commit resolves back to its sessions",
    next: ["opentraces trail track tr_7f31", "opentraces trail graph"],
  },
  {
    prompt: "~/my-project$",
    command: "opentraces ctx step tr_7f31 7",
    pill: "ctx",
    subtitle: "what the agent saw",
    rows: [
      { label: "node", value: "sha256:9b2e…", valCls: "n", status: "resolved" },
      { label: "layers", value: "system · messages · tools · state", valCls: "s", status: "4" },
      { label: "reads", value: "14 observations", valCls: "n", status: "bounded", statusCls: "di" },
      { label: "writes", value: "3 edits", valCls: "n", status: "tracked" },
      { label: "compactions", value: "context trimmed once", status: "1", statusCls: "w" },
    ],
    footer: "Reconstruct the model's view at any step",
    next: ["opentraces ctx resume sha256:9b2e…", "opentraces ctx diff tr_7f31"],
  },
  {
    prompt: "~/my-project$",
    command: "opentraces workflow create eval",
    pill: "workflow",
    subtitle: "project traces into rows",
    rows: [
      { label: "template", value: "command-trajectory-eval", valCls: "s", status: "bundled", statusCls: "di" },
      { label: "script", value: "scripts/build_rows.py", status: "ready" },
      { label: "input", value: "trace slices + bucket refs", status: "bounded", statusCls: "di" },
      { label: "security", value: "regex · entropy", status: "opt-in", statusCls: "di" },
      { label: "rows", value: "schema-valid projection", valCls: "n", status: "typed" },
    ],
    footer: "Workflows turn evidence into dataset rows",
    next: ["opentraces dataset new eval --workflow ./workflows/eval/", "opentraces dataset run eval"],
  },
  {
    prompt: "~/my-project$",
    command: "opentraces dataset publish eval --check-only",
    pill: "dataset",
    subtitle: "reviewed rows to HF",
    rows: [
      { label: "rows", value: "18 projected", valCls: "n", status: "approved" },
      { label: "rejected", value: "0 flagged", valCls: "n", status: "clean" },
      { label: "security", value: "regex · entropy", status: "pass" },
      { label: "bucket", value: "raw evidence", status: "retained private", statusCls: "di" },
      { label: "remote", value: "alice/team-traces", valCls: "s", status: "bound", statusCls: "di" },
    ],
    footer: "Only approved rows leave the bucket",
    next: ["opentraces dataset publish eval", "opentraces dataset schedule eval --daily"],
  },
];

function NextLine({ text }: { text: string }) {
  const isCommand = text.startsWith("opentraces") || text.startsWith("cd ");
  return (
    <span className="terminal-line rail-frame-label">
      <span className="di">{"•"} </span>
      {isCommand ? <span className="c">{text}</span> : <span className="di">{text}</span>}
    </span>
  );
}

function TabPanel({ panel }: { panel: Panel }) {
  return (
    <>
      <span className="terminal-line">
        <span className="p">{panel.prompt}</span> <span className="c">{panel.command}</span>
      </span>
      <span className="terminal-line terminal-line-gap" />
      <div className="rail-frame">
        <span className="terminal-line rail-frame-label">
          <span className="pill">{panel.pill}</span>
          <span className="di">  {panel.subtitle}</span>
        </span>
        {panel.rows.map((r) => (
          <span key={r.label} className="terminal-line wiz-row">
            <span className="n wiz-bullet">{"◇"}</span>
            <span className="di wiz-label">{r.label}</span>
            <span className={`${r.valCls ?? "di"} wiz-val`}>{r.value}</span>
            {r.status && <span className={`${r.statusCls ?? "ok"} wiz-status`}>{r.status}</span>}
          </span>
        ))}
        <span className="terminal-line rail-frame-label">
          <span className="di">{panel.footer}</span>
        </span>
      </div>
      {panel.next.map((n) => (
        <NextLine key={n} text={n} />
      ))}
    </>
  );
}

export default function Hero({ metrics }: { metrics: HeroMetricItem[] }) {
  const [activeTab, setActiveTab] = useState(1);
  const [installIdx, setInstallIdx] = useState(0);

  const panel = HERO_PANELS[activeTab];

  return (
    <section className="hero">
      <div className="hero-grid">
        <div>
          <div className="hero-pill">open traces &nbsp; v{pkg.version}</div>
          <div style={{ height: 16 }} />
          <h1>Open data is the new open source.</h1>
          <p className="hero-sub">
            Every coding session leaves the data you actually want: the prompts, tool calls, reasoning, and edits behind the outcome. When the terminal closes, it is gone.
            <br /><br />
            open<strong>traces</strong> captures it into a private bucket, links each edit to the commit that shipped it, and lets workflows project security-scanned, reviewed dataset rows you choose to share.
          </p>
          <div className="hero-install-tabs">
            {installMethods.map((m, i) => (
              <button
                key={m.label}
                className={`hero-install-tab${i === installIdx ? " active" : ""}`}
                onClick={() => setInstallIdx(i)}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div className="hero-cli-wrap">
            <span className="hero-cli-prefix">{installMethods[installIdx].prefix ?? "$"}</span>
            <span className="hero-cli-input">{installMethods[installIdx].cmd}</span>
            <span
              className="hero-cli-copy"
              title="Copy"
              onClick={() => navigator.clipboard.writeText(installMethods[installIdx].copyText)}
            >[cp]</span>
          </div>
          <div className="hero-actions">
            <Link className="btn btn-primary hero-primary-cta" href="/docs/getting-started/quickstart">[start capturing]</Link>
            <Link
              className="hero-metric-strip"
              href="/explorer"
              aria-label={`Explore community traces. Installs ${metrics[0]?.value ?? "unavailable"}, stars ${metrics[1]?.value ?? "unavailable"}, traces ${metrics[2]?.value ?? "unavailable"}.`}
            >
              <span className="hero-metric-strip-default" aria-hidden="true">
                {metrics.map((metric) => (
                  <span key={metric.label} className="hero-metric-cell" data-metric-type={metric.icon} title={metric.title}>
                    <span className="hero-metric-label">{metric.label}</span>
                    <span className="hero-metric-bottom">
                      <span className="hero-metric-icon">
                        <MetricIcon icon={metric.icon} />
                      </span>
                      <span className="hero-metric-value">{metric.value}</span>
                    </span>
                  </span>
                ))}
              </span>
              <span className="hero-metric-strip-hover" aria-hidden="true">[explore]</span>
            </Link>
          </div>
        </div>
        <div>
          <Terminal
            tabs={tabLabels.map((label, i) => ({
              label,
              active: i === activeTab,
            }))}
            onTabClick={setActiveTab}
          >
            <TabPanel panel={panel} />
          </Terminal>
        </div>
      </div>
    </section>
  );
}
