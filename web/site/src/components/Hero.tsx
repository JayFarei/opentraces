"use client";

import Link from "next/link";
import { useState } from "react";
import Terminal from "./Terminal";
import { AGENT_PROMPT } from "@/lib/agent-prompt";
import pkg from "@/lib/version.json";

const tabLabels = ["init", "status", "review", "push"];
const AGENT_LINES = AGENT_PROMPT.split("\n").length;

const installMethods = [
  { label: "pipx", cmd: "pipx install opentraces", copyText: "pipx install opentraces" },
  { label: "brew", cmd: "brew install [..] opentraces", copyText: "brew install JayFarei/opentraces/opentraces" },
  { label: "skill", cmd: "npx skills add jayfarei/opentraces", copyText: "npx skills add jayfarei/opentraces" },
  { label: "agent", cmd: `agent setup prompt +${AGENT_LINES} lines`, copyText: AGENT_PROMPT, prefix: ">" },
];

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

const tabContents = [InitContent, StatusContent, ReviewContent, PushContent];

export default function Hero() {
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
            Traces are how agents improve. opentraces makes that loop open — commit sessions to HuggingFace Hub, private or public, and let the community build on real workflows instead of synthetic benchmarks.
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
            <Link className="btn btn-primary" href="/docs/getting-started/quickstart">[init your project]</Link>
            <Link className="btn btn-outline" href="/docs/">[documentation]</Link>
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
