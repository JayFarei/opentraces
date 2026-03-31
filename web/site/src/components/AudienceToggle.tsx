"use client";

interface Props {
  audience: "human" | "machine";
  onChange: (a: "human" | "machine") => void;
}

export default function AudienceToggle({ audience, onChange }: Props) {
  return (
    <div className="audience-toggle">
      <button
        className={`audience-toggle-option${audience === "human" ? " active" : ""}`}
        onClick={() => onChange("human")}
        aria-pressed={audience === "human"}
      >
        <span className="audience-dot">{audience === "human" ? "●" : "○"}</span>
        HUMAN
      </button>
      <button
        className={`audience-toggle-option${audience === "machine" ? " active" : ""}`}
        onClick={() => onChange("machine")}
        aria-pressed={audience === "machine"}
      >
        <span className="audience-dot">{audience === "machine" ? "●" : "○"}</span>
        MACHINE
      </button>
    </div>
  );
}
