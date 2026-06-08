"use client";

import { useRef, useState } from "react";
import { ClaudeGlyph, CodexGlyph, PiGlyph } from "./AgentGlyphs";

const RESET_MS = 2000;

export default function CopyPromptButton({ prompt }: { prompt: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleCopy = () => {
    const flip = () => {
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), RESET_MS);
    };

    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(prompt).then(flip, flip);
    } else {
      try {
        const ta = document.createElement("textarea");
        ta.value = prompt;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {
        /* clipboard unavailable — still flip so the user gets feedback */
      }
      flip();
    }
  };

  return (
    <button
      type="button"
      className={`btn btn-primary cta-copy-prompt${copied ? " is-done" : ""}`}
      onClick={handleCopy}
      aria-label="Copy the agent setup prompt to your clipboard"
    >
      <span className="cta-state cta-state-default">
        <span className="cta-agents" aria-hidden="true">
          <ClaudeGlyph className="cta-agent" />
          <CodexGlyph className="cta-agent" />
          <PiGlyph className="cta-agent" />
        </span>
        <span className="cta-label">Copy Prompt</span>
      </span>
      <span className="cta-state cta-state-success" aria-hidden={!copied}>
        <svg className="cta-check" viewBox="0 0 16 16" aria-hidden="true">
          <path
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="square"
            d="M3 8.4 6.4 11.8 13 5.2"
          />
        </svg>
        <span className="cta-label">Copied to clipboard</span>
      </span>
      <span className="cta-live" role="status" aria-live="polite">
        {copied ? "Prompt copied to clipboard" : ""}
      </span>
    </button>
  );
}
