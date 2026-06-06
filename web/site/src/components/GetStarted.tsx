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
  { cmd: "opentraces setup", desc: "one-time machine setup; repos auto-enroll on first session" },
  { cmd: "opentraces trace query --since 7d", desc: "search the private bucket of captured traces" },
  { cmd: "opentraces dataset publish my-dataset", desc: "project rows with a workflow, then publish approved ones to HF" },
];

const agentSteps = [
  { cmd: "set up opentraces on my machine", desc: "walks install/auth/capture choices; repos then auto-enroll on first session" },
  { cmd: "scaffold a dataset and run it over my latest traces", desc: "agent calls dataset new + dataset run to populate rows from your traces" },
  { cmd: "review the dataset rows and approve the safe ones", desc: "agent walks each row, checks redactions, approves what's safe to share" },
  { cmd: "publish the approved rows to HuggingFace", desc: "uploads approved rows to the configured dataset remote" },
];

export default function GetStarted() {
  return (
    <section>
      <SectionRule label="get started" />
      <div className="section-title">Capture privately, publish projected rows.</div>
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
