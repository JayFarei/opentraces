"use client";

import Link from "next/link";
import { useState } from "react";
import Terminal from "./Terminal";
import CopyPromptButton from "./CopyPromptButton";
import InstallsSparkline from "./InstallsSparkline";
import AgentPromptCarousel from "./AgentPromptCarousel";
import pkg from "@/lib/version.json";
import type { HeroMetricItem } from "@/lib/homepage-metrics";

const tabLabels = ["setup", "bucket", "trace", "trail", "ctx", "workflow", "dataset"];

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
      { label: "otel", value: "context-tree capture", status: "ready" },
      { label: "skill", value: "shared agent skill", status: "linked" },
      { label: "hf login", value: "alice-dev", valCls: "s", status: "auth" },
    ],
    footer: "Global tracking on — repos auto-enroll on first capture",
    next: ["opentraces bucket status"],
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

export default function Hero({
  metrics,
  agentPrompt,
  installSeries,
}: {
  metrics: HeroMetricItem[];
  agentPrompt: string;
  installSeries: number[];
}) {
  const [activeTab, setActiveTab] = useState(0);

  const panel = HERO_PANELS[activeTab];
  const installsValue = metrics.find((m) => m.icon === "install")?.value ?? "—";

  return (
    <section className="hero">
      <div className="hero-grid">
        <div className="hero-left">
          <div className="hero-pill-row">
            <div className="hero-pill">opentraces &nbsp; v{pkg.version}</div>
            <Link className="hero-announce" href="/blog/introducing-opentraces-0-4">
              <span aria-hidden="true">←</span> Read the announcement
            </Link>
          </div>
          <div style={{ height: 16 }} />
          <h1>Traces are the new source code.</h1>
          <p className="hero-sub">
            The unit of work is no longer the diff. It is the session you ran to produce it. Every prompt, tool call, and edit is behind the outcome.
            <br /><br />
            opentraces captures every agent session privately, anchors it to Git, and turns it into search, lineage, evals, and datasets.
          </p>
          <div className="hero-cta-stack">
          <div className="hero-cta">
            <CopyPromptButton prompt={agentPrompt} />
            <Link className="cta-secondary" href="/docs">
              <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                <g fill="none" stroke="currentColor" strokeWidth="1.4">
                  <path d="M3.5 1.5 H10 L12.5 4 V14.5 H3.5 Z" />
                  <path d="M10 1.5 V4 H12.5" />
                  <line x1="5.5" y1="7" x2="10.5" y2="7" />
                  <line x1="5.5" y1="9.5" x2="10.5" y2="9.5" />
                  <line x1="5.5" y1="12" x2="9" y2="12" />
                </g>
              </svg>
              <span>View Docs</span>
            </Link>
          </div>
          <Link
            className="hero-installs-card"
            href="/explorer"
            aria-label={`Community installs ${installsValue}. Explore community traces.`}
          >
            <span className="hero-installs-stat">
              <span className="hero-installs-label">Installs</span>
              <span className="hero-installs-value">{installsValue}</span>
            </span>
            <span className="hero-installs-spark-wrap" aria-hidden="true">
              <InstallsSparkline points={installSeries} />
              <span className="hero-installs-cta">
                Explore the community
                <span className="hero-installs-cta-arrow">→</span>
              </span>
            </span>
          </Link>
          </div>
        </div>
        <div className="hero-terminal-col">
          <Terminal
            tabs={tabLabels.map((label, i) => ({
              label,
              active: i === activeTab,
            }))}
            onTabClick={setActiveTab}
            footer={<AgentPromptCarousel />}
          >
            <TabPanel panel={panel} />
          </Terminal>
        </div>
      </div>
    </section>
  );
}
