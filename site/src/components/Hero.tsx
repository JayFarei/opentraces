"use client";

import { useState } from "react";
import Terminal from "./Terminal";

const tabLabels = ["login", "init", "status", "review", "push"];

function LoginContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~$</span> <span className="c">opentraces login</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Authenticating with HuggingFace Hub...</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Open this URL in your browser:</span></span>
      <span className="terminal-line"><span className="di">    </span><span className="s">https://huggingface.co/device</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  And enter code: </span><span className="n">WDJB-MJHT</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Waiting for authorization...</span> <span className="ok">done</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Authenticated as </span><span className="s">jayfarei</span></span>
      <span className="terminal-line"><span className="di">  Token saved to ~/.opentraces/credentials</span></span>
    </>
  );
}

function InitContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces init</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  opentraces: initializing trace collection</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Security tier?</span></span>
      <span className="terminal-line"><span className="di">  [1] danger</span>    <span className="di">auto-share, regex redaction</span></span>
      <span className="terminal-line"><span className="di">  [2] automated</span> <span className="di">scan + review flagged (default)</span>  <span className="s">{"\u2190"}</span></span>
      <span className="terminal-line"><span className="di">  [3] manual</span>    <span className="di">review every session</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="ok">{"\u2713"}</span> <span className="di">Created .opentraces/config.yml</span></span>
      <span className="terminal-line"><span className="ok">{"\u2713"}</span> <span className="di">Installed Claude Code session hook</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Sessions will be collected automatically.</span></span>
    </>
  );
}

function StatusContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces status</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  my-project (tier 2, automated)</span></span>
      <span className="terminal-line"><span className="di">  remote: </span><span className="s">jayfarei/opentraces</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  </span><span className="n">3</span> <span className="di">sessions staged</span></span>
      <span className="terminal-line"><span className="di">  {"\u251C\u2500\u2500"} 2h ago    </span><span className="s">{"\u201C"}refactor auth middleware{"\u201D"}</span>   <span className="n">47</span> <span className="di">steps</span>  <span className="di">0 flags</span></span>
      <span className="terminal-line"><span className="di">  {"\u251C\u2500\u2500"} 5h ago    </span><span className="s">{"\u201C"}fix billing webhook{"\u201D"}</span>        <span className="n">23</span> <span className="di">steps</span>  <span className="w">1 flag {"\u26A0"}</span></span>
      <span className="terminal-line"><span className="di">  {"\u2514\u2500\u2500"} yesterday </span><span className="s">{"\u201C"}add settings page{"\u201D"}</span>          <span className="n">65</span> <span className="di">steps</span>  <span className="di">0 flags</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  0 reviewed {"\u00B7"} 0 pushed</span></span>
    </>
  );
}

function ReviewContent() {
  return (
    <pre className="terminal-tui-mockup">{`┌─ SESSIONS (3 staged) ──────────┬─ DETAIL ─────────────────────────┐
│                                 │                                   │
│  ● "refactor auth"    47 steps  │  refactor auth middleware         │
│  ○ "fix billing"      23 steps  │  claude-code · opus-4-6           │
│  ○ "add settings"     65 steps  │  233s · 42,891 tokens · $3.21    │
│                                 │  tier 2 · 2 redacted · 0 flags   │
│                                 │                                   │
│                                 │  ── steps ──────────────────────  │
│                                 │  [0] user  "refactor the auth.."  │
│                                 │  [1] agent Read auth.py           │
│                                 │  [2] agent Edit auth.py L42-67   │
│                                 │  [3] user  "looks good, also.."   │
│                                 │  [4] agent Write tests/auth.py   │
│                                 │                                   │
├─ approved: 0 · pushed: 0 ──────┴───────────────────────────────────┤
│ j/k navigate · a approve · r reject · p push · q quit             │
└─────────────────────────────────────────────────────────────────────┘`}</pre>
  );
}

function PushContent() {
  return (
    <>
      <span className="terminal-line"><span className="p">~/my-project$</span> <span className="c">opentraces push</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Pushing 3 approved sessions (private)...</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="ok">{"\u2713"}</span> <span className="di">Pushed {"\u2192"}</span> <span className="s">jayfarei/opentraces</span> <span className="di">(private)</span></span>
      <span className="terminal-line"><span className="di">    135 steps {"\u00B7"} 42,891 tokens {"\u00B7"} $3.21 estimated</span></span>
      <span className="terminal-line terminal-line-gap" />
      <span className="terminal-line"><span className="di">  Run </span><span className="c">opentraces push --publish</span><span className="di"> to make public.</span></span>
    </>
  );
}

const tabContents = [LoginContent, InitContent, StatusContent, ReviewContent, PushContent];

export default function Hero() {
  const [activeTab, setActiveTab] = useState(0);

  const ActiveContent = tabContents[activeTab];

  return (
    <section className="hero">
      <div className="hero-grid">
        <div>
          <div className="hero-pill">open traces &nbsp; v0.1.0</div>
          <div style={{ height: 16 }} />
          <h1>Open data is the new open source.</h1>
          <p className="hero-sub">
            Commit your agent traces to HuggingFace Hub. Private or public, like GitHub for code. One init, automatic collection, push when ready.
          </p>
          <div className="hero-cli-wrap">
            <span className="hero-cli-prefix">$</span>
            <span className="hero-cli-input">pip install opentraces</span>
            <span className="hero-cli-copy" title="Copy">[cp]</span>
          </div>
          <div className="hero-actions">
            <a className="btn btn-primary" href="#">[init your project]</a>
            <a className="btn btn-outline" href="/docs/">[documentation]</a>
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
