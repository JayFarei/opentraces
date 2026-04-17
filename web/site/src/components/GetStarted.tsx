"use client";

import Link from "next/link";
import SectionRule from "./SectionRule";

function CopyBox({ cmd, desc }: { cmd: string; desc: string }) {
  return (
    <div className="gs-row">
      <div className="gs-cmd-box">
        <span className="gs-cmd-text">{cmd}</span>
        <button
          className="gs-cmd-cp"
          onClick={() => navigator.clipboard.writeText(cmd)}
          title="Copy to clipboard"
        >[cp]</button>
      </div>
      <div className="gs-desc">{desc}</div>
    </div>
  );
}

const terminalSteps = [
  { cmd: "pipx install opentraces", desc: "install the CLI" },
  { cmd: "opentraces setup && opentraces init", desc: "ensure the global install, then initialize an inbox in this project" },
  { cmd: "opentraces web", desc: "review in the browser, stage what's safe to share" },
  { cmd: "opentraces push", desc: "upload staged traces to your HuggingFace dataset" },
];

const agentSteps = [
  { cmd: "set up opentraces for this project", desc: "installs, authenticates, connects a dataset, and installs the hook" },
  { cmd: "open my opentraces inbox and review my traces", desc: "review in the browser or TUI, then stage or reject traces" },
  { cmd: "ensure $CLIENT is redacted and stage the safe traces", desc: "agent checks redactions against your criteria before upload" },
  { cmd: "push my staged traces to HuggingFace", desc: "uploads the staged traces to your private dataset" },
];

export default function GetStarted() {
  return (
    <section>
      <SectionRule label="get started" />
      <div className="section-title">Start pushing traces in 60 seconds.</div>
      <p className="section-sub">
        Open data is the new open source. Your agent traces are the most valuable dataset
        nobody is collecting. Start contributing to the commons.
      </p>

      <div className="get-started-dual">
        <div className="get-started-col">
          <div className="get-started-col-header">
            <span className="get-started-col-icon">$</span> from your terminal
          </div>
          {terminalSteps.map((s) => (
            <CopyBox key={s.cmd} cmd={s.cmd} desc={s.desc} />
          ))}
        </div>

        <div className="get-started-col">
          <div className="get-started-col-header">
            <span className="get-started-col-icon">&gt;</span> from your agent
          </div>
          {agentSteps.map((s) => (
            <CopyBox key={s.cmd} cmd={s.cmd} desc={s.desc} />
          ))}
        </div>
      </div>

      <div className="hero-actions" style={{ marginTop: 32 }}>
        <Link className="btn btn-primary" href="/docs/getting-started/installation">[get started]</Link>
      </div>
    </section>
  );
}
