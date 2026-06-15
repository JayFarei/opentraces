"use client";

import Link from "next/link";
import SectionRule from "./SectionRule";
import CopyPromptButton from "./CopyPromptButton";

function InstallCmd({ cmd, copy }: { cmd: string; copy?: string }) {
  return (
    <div className="gs-cmd-box">
      <span className="gs-cmd-text">{cmd}</span>
      <button
        className="gs-cmd-cp"
        onClick={() => navigator.clipboard.writeText(copy ?? cmd)}
        title="Copy to clipboard"
      >[cp]</button>
    </div>
  );
}

export default function GetStarted({ agentPrompt }: { agentPrompt: string }) {
  return (
    <section>
      <SectionRule label="get started" />
      <div className="section-title">Capture privately, publish projected rows.</div>
      <p className="section-sub">
        Open data is the new open source. Your agent traces are the most valuable dataset
        nobody is collecting. Nothing leaves your machine until you approve and publish a dataset.
      </p>

      <div className="gs-options">
        <div className="gs-option">
          <div className="gs-option-header">
            <span className="gs-option-icon">&gt;</span> Get my agent to install it
          </div>
          <p className="gs-option-desc">
            Paste the setup prompt into Claude, Codex, or Pi. It installs the CLI,
            shared skill, hooks, auth, and capture with your confirmation.
          </p>
          <div className="gs-option-action">
            <CopyPromptButton prompt={agentPrompt} />
          </div>
        </div>

        <div className="gs-option">
          <div className="gs-option-header">
            <span className="gs-option-icon">$</span> Install it yourself
          </div>
          <p className="gs-option-desc">
            One-line install with pipx or Homebrew, then run <code>opentraces setup</code> once.
          </p>
          <div className="gs-option-action">
            <InstallCmd cmd="pipx install opentraces" />
            <InstallCmd cmd="brew install [...] opentraces" copy="brew install JayFarei/opentraces/opentraces" />
          </div>
        </div>

        <div className="gs-option">
          <div className="gs-option-header">
            <span className="gs-option-icon">?</span> Learn more about it
          </div>
          <p className="gs-option-desc">
            See how capture, datasets, and publishing fit together before you commit.
          </p>
          <div className="gs-option-action">
            <Link className="cta-secondary gs-option-link" href="/docs">
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
        </div>
      </div>
    </section>
  );
}
