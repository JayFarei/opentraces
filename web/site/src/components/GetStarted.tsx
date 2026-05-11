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
  { cmd: "opentraces setup && opentraces init", desc: "ensure the global install, then connect this project's capture hooks" },
  { cmd: "opentraces dataset new my-dataset", desc: "scaffold a local dataset; run, review, and stage rows" },
  { cmd: "opentraces dataset publish my-dataset", desc: "publish approved rows to your HuggingFace dataset" },
];

const agentSteps = [
  { cmd: "set up opentraces for this project", desc: "installs, authenticates, and connects the capture hook" },
  { cmd: "scaffold a dataset and run it over my latest traces", desc: "agent calls dataset new + dataset run to populate rows from your traces" },
  { cmd: "review the dataset rows and approve the safe ones", desc: "agent walks each row, checks redactions, approves what's safe to share" },
  { cmd: "publish the approved rows to HuggingFace", desc: "uploads approved rows to your private dataset" },
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
