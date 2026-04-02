"use client";

import Link from "next/link";
import { useState } from "react";
import Terminal from "./Terminal";
import { AGENT_PROMPT } from "@/lib/agent-prompt";
import pkg from "@/lib/version.json";
import type { HeroMetricItem } from "@/lib/homepage-metrics";

const tabLabels = ["init", "status", "review", "push", "consume"];
const AGENT_LINES = AGENT_PROMPT.split("\n").length;

const installMethods = [
  { label: "pipx", cmd: "pipx install opentraces", copyText: "pipx install opentraces" },
  { label: "brew", cmd: "brew install [..] opentraces", copyText: "brew install JayFarei/opentraces/opentraces" },
  { label: "skill", cmd: "npx skills add jayfarei/opentraces", copyText: "npx skills add jayfarei/opentraces" },
  { label: "agent", cmd: `agent setup prompt +${AGENT_LINES} lines`, copyText: AGENT_PROMPT, prefix: ">" },
];

function MetricIcon({ icon }: { icon: HeroMetricItem["icon"] }) {
  if (icon === "download") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M8 2v7m0 0 3-3m-3 3L5 6M3 12.5h10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="square" />
      </svg>
    );
  }

  if (icon === "star") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="m8 2 1.6 3.3 3.6.5-2.6 2.5.6 3.6L8 10.1 4.8 11.9l.6-3.6L2.8 5.8l3.6-.5Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="miter" />
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

function InitContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces init</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Authenticating with HuggingFace Hub...</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Open this URL in your browser:</span></span>
      <span className="terminal-line"><span className="di">    </span><span className="s">https://hf.co/oauth/device</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  And enter code: </span><span className="n">K7R2-X9FN</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Waiting for authorization....... </span><span className="ok">done</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="ok">{"\u2713"}</span> <span className="di"> Authenticated as </span><span className="s">alice-dev</span></span>
      <span className="terminal-line"><span className="di">  Token saved to ~/.opentraces/credentials</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Review policy?</span></span>
      <span className="terminal-line"><span className="di">  [1] review</span>  <span className="di">sessions land in Inbox for you to review</span>  <span className="s">{"\u2190"}</span></span>
      <span className="terminal-line"><span className="di">  [2] auto</span>    <span className="di">capture, sanitize, commit, and push automatically</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="ok">{"\u2713"}</span> <span className="di"> Created .opentraces/config.json</span></span>
      <span className="terminal-line"><span className="ok">{"\u2713"}</span> <span className="di"> Installed agent session hook</span></span>
    </>
  );
}

function StatusContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces status</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  my-project inbox</span></span>
      <span className="terminal-line"><span className="di">  review policy: </span><span className="s">review</span></span>
      <span className="terminal-line"><span className="di">  remote: </span><span className="s">jayfarei/opentraces</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  inbox </span><span className="n">3</span> <span className="di"> committed </span><span className="n">0</span> <span className="di"> pushed </span><span className="n">0</span></span>
      <span className="terminal-line"><span className="di">  {"\u251C\u2500\u2500"} 2h ago    </span><span className="s">{"\u201C"}refactor auth middleware{"\u201D"}</span>   <span className="n">47</span> <span className="di">steps</span>  <span className="di">inbox</span></span>
      <span className="terminal-line"><span className="di">  {"\u251C\u2500\u2500"} 5h ago    </span><span className="s">{"\u201C"}fix billing webhook{"\u201D"}</span>        <span className="n">23</span> <span className="di">steps</span>  <span className="w">1 flag {"\u26A0"}</span></span>
      <span className="terminal-line"><span className="di">  {"\u2514\u2500\u2500"} yesterday </span><span className="s">{"\u201C"}add settings page{"\u201D"}</span>          <span className="n">65</span> <span className="di">steps</span>  <span className="di">inbox</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  next: opentraces commit --all</span></span>
    </>
  );
}

function ReviewContent() {
  return (
    <div className="terminal-tui-mockup">
      <div className="tui-row tui-header">
        <span className="tui-left">SESSIONS (3 staged)</span>
        <span className="tui-right">DETAIL</span>
      </div>
      <div className="tui-body">
        <div className="tui-left">
          <div className="tui-session tui-active"><span className="tui-dot ok">●</span> &quot;refactor auth&quot; <span className="n">47</span> steps</div>
          <div className="tui-session"><span className="tui-dot">○</span> &quot;fix billing&quot; <span className="n">23</span> steps</div>
          <div className="tui-session"><span className="tui-dot">○</span> &quot;add settings&quot; <span className="n">65</span> steps</div>
        </div>
        <div className="tui-right">
          <div className="tui-detail-title">refactor auth middleware</div>
          <div className="tui-detail-meta">claude-code · opus-4-6</div>
          <div className="tui-detail-meta">233s · 42,891 tokens · $3.21</div>
          <div className="tui-detail-meta">review · 2 redacted · 0 flags</div>
          <div className="tui-detail-sep">steps</div>
          <div className="tui-step"><span className="di">[0]</span> <span className="s">user</span>  &quot;refactor the auth..&quot;</div>
          <div className="tui-step"><span className="di">[1]</span> <span className="f">agent</span> Read auth.py</div>
          <div className="tui-step"><span className="di">[2]</span> <span className="f">agent</span> Edit auth.py L42-67</div>
          <div className="tui-step"><span className="di">[3]</span> <span className="s">user</span>  &quot;looks good, also..&quot;</div>
          <div className="tui-step"><span className="di">[4]</span> <span className="f">agent</span> Write tests/auth.py</div>
        </div>
      </div>
      <div className="tui-row tui-footer">
        <span>inbox: 3 · committed: 0</span>
        <span>j/k navigate · c commit · r reject · q quit</span>
      </div>
    </div>
  );
}

function PushContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces push</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Pushing 3 committed sessions (private)...</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="ok">{"\u2713"}</span> <span className="di">Pushed {"\u2192"}</span> <span className="s">jayfarei/opentraces</span> <span className="di">(private)</span></span>
      <span className="terminal-line"><span className="di">    135 steps {"\u00B7"} 42,891 tokens {"\u00B7"} $3.21 estimated</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Run </span><span className="c">opentraces push --publish</span><span className="di"> to make public.</span></span>
    </>
  );
}

function ConsumeContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~$</span> <span className="c">hf-mount jayfarei/opentraces /mnt/traces</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Starting daemon (pid=</span><span className="n">21104</span><span className="di">) </span><span className="ok">ready</span></span>
      <span className="terminal-line"><span className="di">    stop: hf-mount-daemon stop /mnt/traces</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="p">~$</span> <span className="c">ls -la /mnt/traces/</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  total 3</span></span>
      <span className="terminal-line"><span className="di">  -r--r--r--  jay  staff  </span><span className="n">2.1 MB</span><span className="di">  Mar 29  </span><span className="s">traces-0001.jsonl</span></span>
      <span className="terminal-line"><span className="di">  -r--r--r--  jay  staff  </span><span className="n">1.8 MB</span><span className="di">  Mar 29  </span><span className="s">traces-0002.jsonl</span></span>
      <span className="terminal-line"><span className="di">  -r--r--r--  jay  staff  </span><span className="n">983 KB</span><span className="di">  Mar 30  </span><span className="s">traces-0003.jsonl</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="p">~$</span> <span className="c">grep -c &quot;tool_use&quot; /mnt/traces/*.jsonl</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="s">traces-0001.jsonl</span><span className="di">: </span><span className="n">847</span></span>
      <span className="terminal-line"><span className="s">traces-0002.jsonl</span><span className="di">: </span><span className="n">612</span></span>
      <span className="terminal-line"><span className="s">traces-0003.jsonl</span><span className="di">: </span><span className="n">291</span></span>
    </>
  );
}

const tabContents = [InitContent, StatusContent, ReviewContent, PushContent, ConsumeContent];

export default function Hero({ metrics }: { metrics: HeroMetricItem[] }) {
  const [activeTab, setActiveTab] = useState(0);
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
            open<strong>traces</strong> lets you parse, sanitise and commit those sessions to HuggingFace Hub so you or others can build on real workflows, not synthetic benchmarks.
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
              aria-label={`Explore community traces. Downloads ${metrics[0]?.value ?? "unavailable"}, stars ${metrics[1]?.value ?? "unavailable"}, traces ${metrics[2]?.value ?? "unavailable"}.`}
            >
              <span className="hero-metric-strip-default" aria-hidden="true">
                {metrics.map((metric) => (
                  <span key={metric.label} className="hero-metric-cell" title={metric.title}>
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
            title="opentraces — zsh"
            onTabClick={setActiveTab}
          >
            <ActiveContent />
          </Terminal>
        </div>
      </div>
    </section>
  );
}
