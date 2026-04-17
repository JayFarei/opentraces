"use client";

import { useState } from "react";

interface Props {
  audience: "human" | "machine";
  onChange: (a: "human" | "machine") => void;
}

export default function AudienceToggle({ audience, onChange }: Props) {
  const [humanHref] = useState(() =>
    typeof window === "undefined" ? "?" : window.location.pathname,
  );

  function handle(e: React.MouseEvent, next: "human" | "machine") {
    e.preventDefault();
    onChange(next);
  }

  return (
    <div className="audience-toggle">
      <a
        href={humanHref}
        suppressHydrationWarning
        className={`audience-toggle-option${audience === "human" ? " active" : ""}`}
        onClick={(e) => handle(e, "human")}
        aria-pressed={audience === "human"}
        role="button"
      >
        <span className="audience-dot">{audience === "human" ? "●" : "○"}</span>
        HUMAN
      </a>
      <a
        href="?view=machine"
        className={`audience-toggle-option${audience === "machine" ? " active" : ""}`}
        onClick={(e) => handle(e, "machine")}
        aria-pressed={audience === "machine"}
        role="button"
      >
        <span className="audience-dot">{audience === "machine" ? "●" : "○"}</span>
        MACHINE
      </a>
    </div>
  );
}
