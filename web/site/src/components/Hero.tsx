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

function SetupContent() {
  const rows: [string, string, string][] = [
    ["agents", "connect?", "claude · codex · pi"],
    ["hooks", "install?", "hooks + Pi package"],
    ["git hook", "install?", "post-commit"],
    ["skill", "install?", "registered"],
    ["watcher", "install?", "powers blame"],
    ["bucket", "configure?", "remote sync"],
    ["hf login", "log in?", "alice-dev"],
    ["security", "optional?", "tools off"],
    ["raw bodies", "default?", "off"],
    ["llm review", "optional?", "configured later"],
  ];
  return (
    <>
      <span className="terminal-line"><span className="p">~$</span> <span className="c">opentraces setup</span></span>
      <span className="terminal-line terminal-line-gap" />
      <div className="rail-frame">
        <span className="terminal-line rail-frame-label">
          <span className="pill">opentraces</span>
          <span className="di">  setup wizard v{pkg.version}</span>
        </span>
        {rows.map(([label, q, val]) => (
          <span key={label} className="terminal-line wiz-row">
            <span className="n wiz-bullet">{"\u25C7"}</span>
            <span className="di wiz-label">{label}</span>
            <span className="di wiz-q">{q}</span>
            <span className="ok wiz-ok">{"\u2713"}</span>
            <span className={label === "agents" || label === "hf login" ? "s wiz-val" : "di wiz-val"}>{val}</span>
          </span>
        ))}
        <span className="terminal-line rail-frame-label">
          <span className="di">Next steps</span>
        </span>
      </div>
      <span className="terminal-line rail-frame-label">
        <span className="di">{"\u2022"} </span>
        <span className="c">cd my-project {"&&"} opentraces init</span>
      </span>
      <span className="terminal-line rail-frame-label">
        <span className="di">{"\u2022"} </span>
        <span className="c">opentraces doctor</span>
      </span>
    </>
  );
}

function InitContent() {
  type Row = { label: string; value: string; valCls: string; status?: string; statusCls?: string };
  const rows: Row[] = [
    { label: "traces", value: "128 envelopes", valCls: "f", status: "local", statusCls: "ok" },
    { label: "events", value: "42 batches", valCls: "f", status: "replayable", statusCls: "ok" },
    { label: "blobs", value: "1.8 GB", valCls: "n", status: "verified", statusCls: "ok" },
    { label: "remote", value: "alice/opentraces-bucket", valCls: "s", status: "manual", statusCls: "di" },
    { label: "datasets", value: "separate", valCls: "di", status: "projected rows", statusCls: "di" },
  ];
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces bucket status</span></span>
      <span className="terminal-line terminal-line-gap" />
      <div className="rail-frame">
        <span className="terminal-line rail-frame-label">
          <span className="pill">bucket</span>
          <span className="di">  private capture store</span>
        </span>
        {rows.map((r) => (
          <span key={r.label} className="terminal-line wiz-row">
            <span className="n wiz-bullet">{"\u25C7"}</span>
            <span className="di wiz-label">{r.label}</span>
            <span className={`${r.valCls} wiz-val`}>{r.value}</span>
            {r.status && <span className={`${r.statusCls} wiz-status`}>{r.status}</span>}
          </span>
        ))}
        <span className="terminal-line rail-frame-label">
          <span className="di">Raw evidence is private until bucket sync</span>
        </span>
      </div>
      <span className="terminal-line rail-frame-label"><span className="di">{"\u2022"} traces/v1 envelopes + companions</span></span>
      <span className="terminal-line rail-frame-label"><span className="di">{"\u2022"} blobs/v1 context + raw payloads</span></span>
    </>
  );
}

function StatusContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces trace query --cwd --since 7d</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  candidates </span><span className="n">12</span><span className="di">  source=</span><span className="s">index</span></span>
      <span className="terminal-line"><span className="di">  facets: </span><span className="s">skill, files, survival, intent</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  {"\u251C\u2500\u2500"} </span><span className="n">tr_7f31</span><span className="di"> </span><span className="s">fix billing webhook</span><span className="di">  </span><span className="ok">alive_on_path</span></span>
      <span className="terminal-line"><span className="di">  {"\u251C\u2500\u2500"} </span><span className="n">tr_c42a</span><span className="di"> </span><span className="s">refactor auth middleware</span><span className="di">  </span><span className="ok">alive_transformed</span></span>
      <span className="terminal-line"><span className="di">  {"\u2514\u2500\u2500"} </span><span className="n">tr_91bb</span><span className="di"> </span><span className="s">add settings page</span><span className="di">  </span><span className="w">unknown</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  next: opentraces trace map tr_7f31 --bursts</span></span>
    </>
  );
}

function ReviewContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces ctx step tr_7f31 7</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  node </span><span className="n">sha256:9b2e...</span></span>
      <span className="terminal-line"><span className="di">  layers </span><span className="s">system messages tool_registry runtime_state</span></span>
      <span className="terminal-line"><span className="di">  reads  </span><span className="n">14</span><span className="di">  writes </span><span className="n">3</span><span className="di">  compactions </span><span className="n">1</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  next: </span><span className="c">opentraces ctx resume sha256:9b2e...</span></span>
    </>
  );
}

function BlameContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces trail blame commit ac019172</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">{"\u2502"}</span></span>
      <span className="terminal-line">
        <span className="di">{"\u251C\u2500 "}</span>
        <span className="n">s:6606fc1f</span>
        <span className="di">  </span>
        <span className="ok">+8</span>
        <span className="di"> </span>
        <span className="w">~12</span>
        <span className="di"> -2 fns  </span>
        <span className="di">{" \u2502 "}</span>
        <span className="di">server, graph_api, api +2</span>
      </span>
      <span className="terminal-line">
        <span className="di">{"\u251C\u2500 "}</span>
        <span className="n">s:fe8fe3fd</span>
        <span className="di">  </span>
        <span className="ok">+6</span>
        <span className="di"> ~1 fns      </span>
        <span className="di">{" \u2502 "}</span>
        <span className="di">PushModal (+6)</span>
      </span>
      <span className="terminal-line">
        <span className="di">{"\u251C\u2500 "}</span>
        <span className="n">s:5baac494</span>
        <span className="di">  </span>
        <span className="ok">+7</span>
        <span className="di"> ~1 fns      </span>
        <span className="di">{" \u2502 "}</span>
        <span className="di">ReviewView, TraceSecurityModal</span>
      </span>
      <span className="terminal-line">
        <span className="di">{"\u2570\u2500\u25CF "}</span>
        <span className="f">c:ac019172</span>
        <span className="di">  feat(viewer): hover actions, two-stage push  </span>
        <span className="ok">100%</span>
      </span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line">
        <span className="di">  3 sessions {"\u00B7"} tier=</span>
        <span className="s">tool_emitted</span>
        <span className="di"> {"\u00B7"} coverage=</span>
        <span className="ok">100%</span>
      </span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line">
        <span className="di">  next: </span>
        <span className="c">opentraces trace get 6606fc1f</span>
      </span>
    </>
  );
}

function PushContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces workflow create eval --template skill-command-trajectory-eval-v1</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="ok">{"\u2713"}</span> <span className="di">created workflow </span><span className="s">eval</span></span>
      <span className="terminal-line"><span className="di">    script: scripts/build_rows.py</span></span>
      <span className="terminal-line"><span className="di">    input: Trace Slices + bucket refs</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  next: </span><span className="c">opentraces dataset new eval-rows --workflow ./workflows/eval/</span></span>
    </>
  );
}

function ConsumeContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces dataset publish eval-rows --check-only</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  rows </span><span className="n">18</span><span className="di">  approved </span><span className="n">18</span><span className="di">  rejected </span><span className="n">0</span></span>
      <span className="terminal-line"><span className="di">  security tools </span><span className="s">regex,entropy</span><span className="di">  policy </span><span className="s">pass</span></span>
      <span className="terminal-line"><span className="di">  bucket evidence </span><span className="ok">retained private</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  next: </span><span className="c">opentraces dataset publish eval-rows</span></span>
    </>
  );
}

const tabContents = [SetupContent, InitContent, StatusContent, ReviewContent, BlameContent, PushContent, ConsumeContent];

export default function Hero({ metrics }: { metrics: HeroMetricItem[] }) {
  const [activeTab, setActiveTab] = useState(1);
  const [installIdx, setInstallIdx] = useState(0);

  const ActiveContent = tabContents[activeTab];

  return (
    <section className="hero">
      <div className="hero-grid">
        <div>
          <div className="hero-pill">open traces &nbsp; v{pkg.version}</div>
          <div style={{ height: 16 }} />
          <h1>Open data is the new open source.</h1>
          <p className="hero-sub">
            When LLMs drive the logic, traces become the real source: the record of decisions, tool calls, and reasoning behind the outcome.
            <br /><br />
            open<strong>traces</strong> captures sessions into a private bucket, lets workflows project compliant dataset rows, and publishes only the reviewed rows you choose to share.
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
            <Link className="btn btn-primary hero-primary-cta" href="/docs/getting-started/quickstart">[init your project]</Link>
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
            <ActiveContent />
          </Terminal>
        </div>
      </div>
    </section>
  );
}
