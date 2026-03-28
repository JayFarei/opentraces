"use client";

import { useState } from "react";

const tiers = [
  {
    id: "open",
    name: "tier 1: open",
    nameStyle: { color: "var(--red)" },
    desc: "Regex scan only. For open-source and benchmarks.",
    tag: null,
  },
  {
    id: "guarded",
    name: "tier 2: guarded",
    nameStyle: {},
    desc: "Classifier + escalation. Flagged traces need review.",
    tag: "default",
  },
  {
    id: "strict",
    name: "tier 3: strict",
    nameStyle: {},
    desc: "Full human-in-the-loop. Nothing leaves without approval.",
    tag: null,
  },
];

export default function SecurityTiers() {
  const [selected, setSelected] = useState("guarded");

  return (
    <div>
      {tiers.map((t) => (
        <label
          key={t.id}
          className={`tier${selected === t.id ? " active" : ""}`}
          onClick={() => setSelected(t.id)}
        >
          <input
            type="radio"
            name="tier"
            checked={selected === t.id}
            onChange={() => setSelected(t.id)}
          />
          <div>
            <div className="tier-name" style={t.nameStyle}>
              {t.name}
              {t.tag && <span className="tier-tag">{t.tag}</span>}
            </div>
            <div className="tier-desc">{t.desc}</div>
          </div>
        </label>
      ))}
    </div>
  );
}
